"""L1 素材登记：一次输入 → `raw/{id}/job.json`。

job.json 不是跨层契约，只是 L1 给 L2 的备忘录：
`id / type / material / images / platform / native_id / meta / warnings / origin`。

三条原则
--------
1. **id 取内容指纹**：改名、移动、重新打包都不变，重复处理只会命中同一条。
2. **拿不到就留空**：元信息从不猜测，宁可缺作者也不填编造的。
3. **只写项目内的 raw/**：不复制大媒体（视频 / 音频只记绝对路径），图片集除外
   （图片本来就在项目里，作为图文素材的本地副本）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core import config as config_mod
from core import fingerprint, paths, route
from l1 import ziputil

JOB_FILE = "job.json"

# 压缩包套压缩包最多解一层：再深就不是素材包，而是套娃
MAX_ARCHIVE_DEPTH = 2


class IngestError(Exception):
    """登记失败。消息面向用户。"""


def register(
    path: str | Path,
    *,
    cfg: config_mod.Config | None = None,
    url: str | None = None,
    force: bool = False,
) -> list[dict]:
    """登记一次输入。

    Args:
        path:  文件 / 目录 / zip
        url:   可选链接，只用于元信息增强（绝不下载）
        force: 忽略已存在的 job.json 重新登记

    Returns:
        job 列表（目录或压缩包会产生多条）
    """
    cfg = cfg or config_mod.get()
    origin = str(path)
    jobs: list[dict] = []
    for task in _expand(path, cfg):
        jobs.append(_register_one(task, cfg, url=url, force=force, origin=origin))
    return jobs


def _expand(path: str | Path, cfg: config_mod.Config, depth: int = 0) -> list[route.Task]:
    """把输入展开成叶子任务：压缩包就地解压后再判定。"""
    tasks = route.resolve(path)
    expanded: list[route.Task] = []
    for task in tasks:
        if task.kind != route.ARCHIVE:
            expanded.append(task)
            continue
        if depth >= MAX_ARCHIVE_DEPTH:
            raise IngestError(
                f"压缩包嵌套过深，已停止：{task.path}\n"
                f"  请先手动解压到一层再处理。"
            )
        dest = _unzip(task.path, cfg)
        expanded.extend(_expand(dest, cfg, depth + 1))
    return expanded


def _unzip(archive: Path, cfg: config_mod.Config) -> Path:
    """解压到 `raw/.unzip/{压缩包指纹}/`，同一个包重复处理会复用目录。"""
    try:
        digest = fingerprint.file_fingerprint(archive)
    except OSError:
        digest = fingerprint.short_hash(archive.name)

    dest = ziputil.unzip_root(digest)
    if not dest.exists() or not any(dest.iterdir()):
        ziputil.extract_zip(archive, dest, max_total_mb=cfg.ingest.max_unzip_mb)
    return dest


# ------------------------------------------------------------------ 单条登记
def _register_one(
    task: route.Task,
    cfg: config_mod.Config,
    *,
    url: str | None,
    force: bool,
    origin: str,
) -> dict:
    warnings: list[str] = []

    if task.kind == route.IMAGE_SET:
        images = list(task.images)
        if not images:
            raise IngestError(f"图文素材里没有图片：{task.path}")
        item_id = fingerprint.image_set_fingerprint(images)
        material = ""
        # 目录名 / 压缩包名用来解析标题与平台
        label = task.path.name if task.path.is_dir() else task.path.parent.name
        base_dir = task.path if task.path.is_dir() else task.path.parent
    else:
        images = []
        try:
            item_id = fingerprint.file_fingerprint(task.path)
        except OSError as e:
            # 读不到内容（权限异常等）时退回文件名 —— 不稳定，但至少能跑下去
            warnings.append(f"读取文件内容失败（{e}），id 已退化为文件名哈希。")
            item_id = fingerprint.short_hash(task.path.name)
        material = str(task.path)
        label = task.path.name
        base_dir = task.path.parent

    job_path = paths.raw_dir(item_id) / JOB_FILE
    if job_path.exists() and not force:
        cached = _read(job_path)
        if cached.get("id") and _job_complete(cached):
            cached["_cache_hit"] = True
            cached["_job_path"] = str(job_path)
            return cached

    info_json, link = route.read_sidecar(Path(label).stem, base_dir)

    if url:
        extra, warn = _enrich_from_url(url, cfg, job_path.parent)
        if warn:
            warnings.append(warn)
        if extra:
            info_json = route.merge_meta(info_json or {}, extra)

    spec = route.detect_platform(label) or route.detect_platform(url or "")
    native = route.native_id(spec, label, url or "", info_json=info_json)
    source_url = url or link or (spec.url_for(native) if spec else "")

    meta = route.assemble_meta(info_json, route.title_from_filename(Path(label).stem))
    meta["source_url"] = source_url
    if not meta.get("title"):
        warnings.append("没有解析出标题，入库时需要人工补一个。")

    job = {
        "id": item_id,
        "type": task.kind,
        "material": material,
        "images": [str(p) for p in images],
        "platform": spec.name if spec else "",
        "native_id": native,
        "meta": meta,
        "warnings": warnings,
        "origin": origin,
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    job["_cache_hit"] = False
    job["_job_path"] = str(job_path)
    return job


def _enrich_from_url(url: str, cfg: config_mod.Config, out_dir: Path) -> tuple[dict | None, str | None]:
    """用 yt-dlp 轻量取元信息（`--skip-download`）。

    失败只记 warning，绝不阻断 —— 元信息是锦上添花，不是流程的前提。
    """
    cmd = [
        sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
        "--skip-download", "--dump-single-json", url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=cfg.ingest.timeout_sec
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"--url 元信息探测失败（{e}），已跳过。"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        return None, f"--url 元信息探测失败（yt-dlp 退出 {proc.returncode}），已跳过：{tail}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, f"--url 元信息探测输出不是 JSON，已跳过：{e}"

    try:
        (out_dir / "meta.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return data, None


# ------------------------------------------------------------------ 查询
def scan(inbox_dir: str | Path) -> list[tuple[Path, str | None, str]]:
    """列出收件目录里有什么（不落盘）。返回 (路径, 类型或 None, 说明)。"""
    d = paths.expand(inbox_dir)
    if not d.is_dir():
        return []

    items: list[tuple[Path, str | None, str]] = []
    for entry in sorted(d.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            try:
                tasks = route.resolve_dir(entry)
            except route.RouteError:
                items.append((entry, None, "未识别（无媒体也无图片）"))
                continue
            kind = tasks[0].kind if len(tasks) == 1 else "batch"
            note = f"一条 {tasks[0].kind}" if len(tasks) == 1 else f"批量 {len(tasks)} 项"
            items.append((entry, kind, note))
        else:
            kind = route.kind_of(entry)
            items.append((entry, kind, kind or "未识别"))
    return items


def load_jobs() -> list[dict]:
    """读出已登记的全部 job（供状态命令使用）。"""
    jobs: list[dict] = []
    if not paths.RAW_DIR.is_dir():
        return jobs
    for job_path in sorted(paths.RAW_DIR.glob(f"*/{JOB_FILE}")):
        job = _read(job_path)
        if job.get("id"):
            job["_job_path"] = str(job_path)
            jobs.append(job)
    return jobs


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _job_complete(job: dict) -> bool:
    """素材是否还在登记时记的那个位置。

    素材被改名或移走后，job 里的绝对路径就失效了 —— 此时必须重新登记
    （id 不变，只更新路径），否则 L2 会直接报「素材不存在」。
    """
    if job.get("type") == route.IMAGE_SET:
        images = job.get("images") or []
        return bool(images) and all(Path(str(p)).exists() for p in images)
    material = job.get("material")
    return bool(material) and Path(str(material)).exists()
