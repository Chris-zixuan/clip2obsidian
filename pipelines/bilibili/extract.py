"""B站提取层（L2）：source.json → clip.json。

视频正文走 `pipelines/common.build_video_transcript`（本地字幕 → ASR）。
差异只在元信息来源：B站下载文件名常带 `_哔哩哔哩_bilibili` 后缀、或形如
`[BV1xxxxxxxx]` 的 BV 号——这些已在本地导入层解析进 source 的 meta / platform_id，
本层只消费。
"""

from __future__ import annotations

import json
from pathlib import Path

from core import config as config_mod
from core import paths, schema
from pipelines import common

PLATFORM = "bilibili"


class ExtractError(common.ExtractError):
    """提取失败。消息面向用户。"""


# ------------------------------------------------------------------ 主流程
def extract(source_path: Path, *, cfg: config_mod.Config | None = None, force: bool = False) -> schema.Clip:
    """读 L1 的 source.json，产出 clip.json。"""
    cfg = cfg or config_mod.get()
    source_path = Path(source_path)
    if not source_path.exists():
        raise ExtractError(f"找不到采集结果：{source_path}\n  请先跑 L1（ingest）。")

    source = json.loads(source_path.read_text(encoding="utf-8"))

    clip_id = f"{PLATFORM}:{source['platform_id']}"
    clip = schema.new_clip(
        platform=PLATFORM,
        platform_id=source["platform_id"],
        content_type="video",
        source_url=source["source_url"],
        canonical_url=source.get("canonical_url") or source["source_url"],
        fetched_at=source.get("fetched_at") or common.now_iso(),
    )

    clip.meta = common.build_meta(source.get("meta") or {})
    clip.provenance.fetcher = source.get("fetcher") or ""
    clip.provenance.warnings.extend(source.get("warnings") or [])

    media = source.get("media") or {}
    media_path = None
    if media.get("path"):
        media_path = paths.PROJECT_ROOT / media["path"]
        clip.assets.append(schema.Asset(kind="video", path=media["path"]))

    # 内容主体：本地字幕 → ASR
    vt = common.build_video_transcript(clip_id, media_path, cfg, force=force)
    clip.provenance.extractor = vt.extractor
    clip.provenance.warnings.extend(vt.warnings)
    clip.content.transcript = [
        schema.Segment(start=s["start"], end=s["end"], text=s["text"]) for s in vt.segments
    ]

    clip.save(paths.work_path(clip_id, ".clip.json"))
    return clip
