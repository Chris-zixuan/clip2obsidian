"""本地导入（L1）：本地视频文件 → source.json。

核心约束
--------
- `media.path` 是**绝对路径**——大视频不复制进 raw/。
  关键便利：pathlib 里 `Path("/proj") / "/abs/video.mp4"` 会直接返回
  `/abs/video.mp4`，所以 `paths.PROJECT_ROOT / media["path"]` 天然兼容绝对路径。
- 幂等：source.json 存在且完整就跳过；`--force` 强制重做。

元信息分层降级（铁律：拿不到就留空，绝不猜测）
------------------------------------------
1. sidecar：同目录同名 .json（yt-dlp info.json）/ .url / .txt
2. `--url` 增强：yt-dlp --dump-single-json --skip-download（只取元信息，绝不下载）
3. 文件名解析：B站的 `_哔哩哔哩_bilibili` 后缀
4. 以上都拿不到 → 留空
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from core import config as config_mod
from core import paths, registry
from pipelines import common

PLATFORM = "local"

# yt-dlp info.json 里我们关心的字段（只取这些，避免把 webpage_url 等噪声写进去）
_META_KEYS = (
    "title", "uploader", "channel", "owner", "author", "description",
    "upload_date", "duration", "tags", "like_count", "collect_count",
    "comment_count", "repost_count", "author_id", "uploader_id",
)


class IngestError(Exception):
    """导入失败。消息面向用户。"""


# ------------------------------------------------------------------ 入口
def ingest(path, *, url=None, cfg=None, force=False) -> dict:
    """把一个本地视频文件导入为 source.json。

    Args:
        path:     视频文件
        url:      可选链接，用于轻量元信息增强（不下载）
        cfg:      配置
        force:    忽略缓存重新导入
    """
    cfg = cfg or config_mod.get()
    p = Path(path).expanduser()

    spec = registry.detect_local(p)
    if spec is None:
        raise IngestError(
            f"无法从输入识别平台：{p}\n"
            f"  文件名需带平台特征（如 B站的「哔哩哔哩」或 BV 号），"
            f"或显式指定 --platform。"
        )

    p = p.expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise IngestError(f"找不到视频文件：{p}")

    pid = _stable_id(p)
    clip_id = f"{spec.name}:{pid}"
    outdir = paths.raw_dir(clip_id)
    source_path = outdir / "source.json"

    # 幂等：已有完整记录直接复用（不触网）
    if source_path.exists() and not force and _cache_complete(_read(source_path)):
        cached = _read(source_path)
        cached["_cache_hit"] = True
        cached["_source_path"] = str(source_path)
        return cached

    warnings: list[str] = []

    # 1) sidecar：同目录同名 .json / .url
    info_json, link = _read_sidecar(p.stem, p.parent)

    # 2) --url 轻量元信息增强（只取 JSON，绝不下载）
    if url:
        extra, warn = _enrich_from_url(url, cfg, outdir)
        if warn:
            warnings.append(warn)
        if extra:
            info_json = _merge_meta(info_json or {}, extra)

    # 3) 文件名解析标题
    filename_title = _title_from_filename(p.stem)

    # 原生 id（B站的 BV 号）只用于拼链接与展示，不参与 clip id ——
    # 否则用户把文件名里的 BV 后缀删掉，同一个视频就会被当成新条目。
    native_id = _native_id(spec, p, info_json, url)

    source_url = url or link or _synth_url(native_id)
    source = {
        "platform": spec.name,
        "platform_id": pid,
        "native_id": native_id,
        "source_url": source_url,
        "canonical_url": source_url,
        "fetched_at": common.now_iso(),
        "fetcher": "local-import",
        "origin_path": str(p),
        "media": {"path": str(p), "kind": "video"},
        "meta": _assemble_meta(info_json, filename_title),
        "warnings": warnings,
    }
    source_path.write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    source["_cache_hit"] = False
    source["_source_path"] = str(source_path)
    return source


def scan(inbox_dir) -> list[tuple[Path, "registry.PlatformSpec | None", str]]:
    """列出收件目录里可识别的待处理项（不落盘）。返回 (路径, 平台或None, 说明)。"""
    d = Path(inbox_dir).expanduser()
    if not d.exists() or not d.is_dir():
        return []
    out: list[tuple[Path, "registry.PlatformSpec | None", str]] = []
    for entry in sorted(d.iterdir()):
        if entry.name.startswith("."):
            continue
        spec = registry.detect_local(entry)
        out.append((entry, spec, spec.label if spec else "未识别（--platform 指定）"))
    return out


# ------------------------------------------------------------------ id 推导
# 指纹取样大小：头尾各 1 MB。足够区分不同视频，又不必读整个大文件。
_FP_CHUNK = 1 << 20


def _stable_id(p: Path) -> str:
    """clip id —— 只依赖文件内容，不依赖文件名与路径。

    曾按文件名 hash，结果是「视频改名 = 全新条目 = 重复落库」。改名为
    `xxx.mp4` → `未命名.mp4` 就要重跑整条流水线，还得手动去重。
    """
    try:
        return _content_fingerprint(p)
    except OSError:
        # 读不到内容（权限、异常文件）时退回文件名 —— 不稳定，但至少能跑下去
        return _sha1(p.stem)


def _content_fingerprint(p: Path) -> str:
    """内容指纹 = sha1(文件大小 + 头 1MB + 尾 1MB)。

    为什么不直接 hash 整个文件：一个 2 GB 的视频全量哈希要十几秒，而 L1 常常
    一次扫整个收件目录。头尾采样对区分「不同视频」已经足够（封装容器的头部
    元数据本就不同），且恒定耗时。
    """
    size = p.stat().st_size
    h = hashlib.sha1(str(size).encode("ascii"))
    with p.open("rb") as f:
        h.update(f.read(_FP_CHUNK))
        if size > _FP_CHUNK:
            f.seek(-_FP_CHUNK, 2)
            h.update(f.read(_FP_CHUNK))
    return h.hexdigest()[:12]


def _native_id(
    spec: registry.PlatformSpec, p: Path, info_json: dict | None = None, url: str | None = None
) -> str:
    """原生 id（B站的 BV 号），拿不到返回空串。

    来源优先级：文件名 → 命令行给的 --url → sidecar 的 info.json。
    只用于拼规范链接与展示，**不参与 clip id**。
    """
    for src in (p.name, url or ""):
        if found := spec.extract_id(src):
            return found
    v = (info_json or {}).get("id")
    if isinstance(v, str) and v.upper().startswith("BV"):
        return v
    return ""


def _title_from_filename(stem: str) -> str:
    """从文件名解析标题：剥掉 B站下载器的固定后缀。"""
    s = re.sub(r"_哔哩哔哩_bilibili$", "", stem, flags=re.I)
    s = re.sub(r"[_\-]?bilibili$", "", s, flags=re.I)
    s = re.sub(r"[_\-]?哔哩哔哩$", "", s)
    return s.strip()


def _synth_url(native_id: str) -> str:
    """有原生 id 就拼出规范链接（便于笔记里放来源），拼不出则留空。"""
    if native_id.upper().startswith("BV"):
        return f"https://www.bilibili.com/video/{native_id}"
    return ""


def _sha1(text: str) -> str:
    """12 位短哈希。只在读不到文件内容时兜底用。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _read_sidecar(base: str, search_dir: Path) -> tuple[dict | None, str | None]:
    """读同目录同名 sidecar：.json（元信息）/ .url（链接）。

    兼容 yt-dlp 的 `{base}.info.json`——其 stem 是 `{base}.info`，匹配时先剥掉。
    """
    info_json = None
    link = None
    for f in search_dir.iterdir():
        if not f.is_file():
            continue
        cand = f.stem[:-len(".info")] if f.stem.endswith(".info") else f.stem
        if cand != base:
            continue
        suf = f.suffix.lower()
        if suf == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    info_json = data
            except (json.JSONDecodeError, OSError):
                pass
        elif suf == ".url":
            link = link or _first_url(f.read_text(encoding="utf-8", errors="ignore"))
    return info_json, link


def _first_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s\"'<>]+", text or "")
    return m.group(0).rstrip(".,;:!?）】") if m else None


def _assemble_meta(info_json: dict | None, filename_title: str) -> dict:
    """把 sidecar/url 元信息整理成精简 meta；缺失字段留空，绝不补。"""
    meta: dict = {}
    if info_json:
        meta["title"] = info_json.get("title") or ""
        meta["author"] = (info_json.get("uploader") or info_json.get("channel")
                          or info_json.get("owner") or "").strip()
        meta["description"] = info_json.get("description") or ""
        ud = str(info_json.get("upload_date") or "")
        meta["published"] = (f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}"
                             if len(ud) == 8 and ud.isdigit() else "")
        meta["duration_sec"] = int(info_json.get("duration") or 0)
    if filename_title and not meta.get("title"):
        meta["title"] = filename_title
    for k in ("title", "author", "description"):
        meta[k] = (meta.get(k) or "").strip()
    return meta


def _merge_meta(base: dict, extra: dict) -> dict:
    """把 --url 探来的 yt-dlp 元信息并入本地 sidecar 元信息（extra 只补缺失字段）。"""
    merged = dict(base)
    for k in _META_KEYS:
        v = extra.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        if isinstance(v, (int, float)) and v == 0:
            continue
        merged[k] = v
    return merged


def _enrich_from_url(url: str, cfg, outdir) -> tuple[dict | None, str | None]:
    """用 yt-dlp 轻量取元信息 JSON（--skip-download）。失败只返回 warning，不阻断。"""
    cmd = [
        cfg.tools.python_bin, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
        "--skip-download", "--dump-single-json", url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=cfg.ingest.timeout_sec * 3
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, f"--url 元信息探测失败（{e}），已跳过。"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-300:]
        return None, f"--url 元信息探测失败（yt-dlp 退出 {proc.returncode}），已跳过：{tail}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, f"--url 元信息探测输出非 JSON，已跳过：{e}"
    if outdir:
        (Path(outdir) / "meta.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return data, None


# ------------------------------------------------------------------ 幂等
def _cache_complete(source: dict) -> bool:
    media = source.get("media") or {}
    if not media.get("path"):
        return False
    return (paths.PROJECT_ROOT / media["path"]).exists()


def _read(source_path: Path) -> dict:
    try:
        return json.loads(source_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
