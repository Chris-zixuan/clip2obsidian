"""抖音采集层（L1）：链接 → 原始物料。

产出：raw/douyin_{id}/ 下
  - meta.json      yt-dlp 的完整元信息 dump（保留全量，便于排查）
  - source.json    L1 物料清单，供 L2 extract 读取
  - media.mp4      仅当未命中字幕时下载
  - subtitle.*.srt 命中平台字幕时

设计要点
--------
- cookie 来源从配置读（`cfg.fetch.cookie_args`），不硬编码浏览器名。
- 字幕优先：命中字幕就不下载视频，既快又没有同音字错误。
- 幂等：source.json 存在且完整即跳过，`force=True` 强制重抓。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from core import config as config_mod
from core import paths

PLATFORM = "douyin"

# 字幕语言优先级：简体 > 中文通用 > 英文
_LANG_PRIORITY = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "en")


class FetchError(Exception):
    """采集失败。消息面向用户，包含可执行的下一步建议。"""


# ------------------------------------------------------------------ 主流程
def fetch(url: str, *, cfg: config_mod.Config | None = None, force: bool = False) -> dict:
    """抓取一条抖音链接的原始物料，返回 source.json 的内容。"""
    cfg = cfg or config_mod.get()
    py = cfg.tools.python_bin
    paths.ensure_dirs()

    _check_ytdlp(py)

    # 1. 元信息（顺带拿到真实 id 与规范化链接）
    meta = _dump_meta(url, cfg, py)
    vid = str(meta.get("id") or "").strip()
    if not vid:
        raise FetchError(
            "yt-dlp 返回的元信息里没有 id，无法定位作品。\n"
            "  常见原因：链接已失效、作品已删除、或需要登录态 cookie。"
        )

    outdir = paths.raw_dir(f"{PLATFORM}:{vid}")
    meta_path = outdir / "meta.json"
    source_path = outdir / "source.json"

    # 2. 幂等：已有完整物料直接复用
    if source_path.exists() and not force:
        cached = json.loads(source_path.read_text(encoding="utf-8"))
        if _cache_complete(cached):
            cached["_cache_hit"] = True
            cached["_source_path"] = str(source_path)
            return cached

    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    warnings: list[str] = []
    ytdlp_version = _run([py, "-m", "yt_dlp", "--version"]).strip() or "unknown"

    # 3. 字幕优先
    subtitle: dict | None = None
    if cfg.fetch.prefer_subtitle:
        subtitle = _try_subtitle(meta, outdir, url, cfg, py, warnings)

    # 4. 无字幕才下载视频
    media: dict | None = None
    if subtitle is None:
        media = _download_media(outdir, url, cfg, py, warnings)

    source = {
        "platform": PLATFORM,
        "platform_id": vid,
        "source_url": url,
        "canonical_url": _clean_url(meta.get("webpage_url") or url),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fetcher": f"yt-dlp@{ytdlp_version}",
        "meta_path": _rel(meta_path),
        "subtitle": subtitle,
        "media": media,
        "warnings": warnings,
    }
    source_path.write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    source["_cache_hit"] = False
    # 内部字段：方便调用方直接取路径，不写进 source.json
    source["_source_path"] = str(source_path)
    return source


# ------------------------------------------------------------------ 步骤实现
def _check_ytdlp(py: str) -> None:
    try:
        _run([py, "-m", "yt_dlp", "--version"])
    except FetchError as e:
        raise FetchError(
            f"这个 Python 里没有可用的 yt-dlp：{py}\n"
            f"  请在 config.toml 的 [tools].python 里指向装好 yt-dlp 的解释器。\n"
            f"  原始错误：{e}"
        ) from e


def _dump_meta(url: str, cfg: config_mod.Config, py: str) -> dict:
    """取元信息。这里是唯一会因网络抖动失败的步骤。"""
    cmd = [
        py, "-m", "yt_dlp",
        "--no-warnings", "--no-playlist",
        "--dump-single-json",
        *cfg.fetch.cookie_args,
        url,
    ]
    try:
        out = _run(cmd, timeout=cfg.fetch.timeout_sec * 3)
    except FetchError as e:
        raise FetchError(_diagnose(str(e), url)) from e
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise FetchError(f"yt-dlp 输出不是合法 JSON：{e}") from e


def _try_subtitle(meta: dict, outdir: Path, url: str, cfg, py: str, warnings: list[str]) -> dict | None:
    """尝试取平台字幕。命中返回字幕信息，否则返回 None。"""
    manual: dict = meta.get("subtitles") or {}
    auto: dict = meta.get("automatic_captions") or {}

    for kind, table in (("manual", manual), ("auto", auto)):
        lang = _pick_lang(table)
        if not lang:
            continue
        flag = "--write-subs" if kind == "manual" else "--write-auto-subs"
        cmd = [
            py, "-m", "yt_dlp",
            "--no-warnings", "--no-playlist", "--skip-download",
            flag, "--sub-langs", lang, "--convert-subs", "srt",
            "-o", str(outdir / "subtitle"),
            *cfg.fetch.cookie_args,
            url,
        ]
        try:
            _run(cmd, timeout=cfg.fetch.timeout_sec * 3)
        except FetchError as e:
            warnings.append(f"{kind} 字幕 {lang} 下载失败，回退到视频转写：{e}")
            continue

        files = sorted(outdir.glob("subtitle*.srt"))
        if files:
            return {"path": _rel(files[0]), "lang": lang, "kind": kind}

    return None


def _download_media(outdir: Path, url: str, cfg, py: str, warnings: list[str]) -> dict:
    """下载视频本体。"""
    cmd = [
        py, "-m", "yt_dlp",
        "--no-warnings", "--no-playlist",
        "-f", cfg.fetch.video_format,
        "-o", str(outdir / "media.%(ext)s"),
        *cfg.fetch.cookie_args,
        url,
    ]
    try:
        _run(cmd)
    except FetchError as e:
        raise FetchError(_diagnose(str(e), url)) from e

    files = [p for p in sorted(outdir.glob("media.*")) if p.suffix != ".part"]
    if not files:
        raise FetchError(f"下载完成但没找到媒体文件，目录：{outdir}")
    return {"path": _rel(files[0]), "kind": "video"}


# ------------------------------------------------------------------ 工具函数
def _pick_lang(table: dict) -> str | None:
    """按优先级挑一个可用字幕语言。"""
    if not table:
        return None
    keys = set(table)
    for lang in _LANG_PRIORITY:
        if lang in keys:
            return lang
    # 兜底：任意 zh* 轨道
    for k in sorted(keys):
        if k.lower().startswith("zh"):
            return k
    return None


def _cache_complete(source: dict) -> bool:
    """缓存是否可用：物料清单存在且其指向的文件都还在。"""
    root = paths.PROJECT_ROOT
    meta = source.get("meta_path")
    if not meta or not (root / meta).exists():
        return False
    media = source.get("media") or {}
    sub = source.get("subtitle") or {}
    need = [x.get("path") for x in (media, sub) if x.get("path")]
    if not need:
        return False  # 既无字幕也无媒体 → 不完整
    return all((root / p).exists() for p in need)


def _rel(p: Path) -> str:
    """转成相对项目根的路径，便于整体搬移。"""
    try:
        return str(Path(p).resolve().relative_to(paths.PROJECT_ROOT))
    except ValueError:
        return str(p)


def _clean_url(url: str) -> str:
    """去掉分享链接带的跟踪参数。

    抖音的 `webpage_url` 常带 `?previous_page=app_code_link`，写进笔记的
    source 字段不优雅。抖音无需靠 query 访问，直接清空即可。
    （小红书不同：它必须保留 xsec_token，见该平台实现。）
    """
    from urllib.parse import urlsplit, urlunsplit

    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, s.path, "", ""))


def _run(cmd: list[str], timeout: int | None = None) -> str:
    """执行外部命令，失败时抛出带 stderr 的 FetchError。"""
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired as e:
        raise FetchError(f"命令超时（{timeout}s）：{' '.join(cmd[:3])} …") from e
    except FileNotFoundError as e:
        raise FetchError(f"命令不存在：{cmd[0]}") from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise FetchError(f"命令失败（exit {proc.returncode}）：\n{detail[-1500:]}")
    return proc.stdout or ""


def _diagnose(stderr: str, url: str) -> str:
    """把 yt-dlp 的原始报错翻译成可执行的建议。"""
    low = stderr.lower()
    prefix = f"抓取失败：{url}\n"

    if "504" in stderr or "timed out" in low or "timeout" in low:
        return (
            prefix
            + "  看起来是网络/代理瞬时抖动（本机代理偶发 504）。\n"
            + "  先验证链路：curl -s -o /dev/null -w '%{http_code}' -I <链接>（应返回 302）\n"
            + "  若正常，直接重跑即可，不必改配置。"
        )
    if "sign in" in low or "login" in low or "cookies" in low or "账号" in stderr:
        return (
            prefix
            + "  需要登录态。请确认：\n"
            + "  1) 已在 Edge 里登录抖音；\n"
            + "  2) config.toml 的 [fetch].browser 是 edge；\n"
            + "  3) macOS 弹出钥匙串授权时点了「允许」。"
        )
    if "unavailable" in low or "not exist" in low or "404" in stderr:
        return prefix + "  作品可能已删除或设为私密。"
    return prefix + f"  原始错误：\n{stderr[-1200:]}"
