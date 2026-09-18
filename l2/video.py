"""视频分支：抽音轨 → 本地字幕短路 → 分片转写 → 纯正文 md。

顺带承载音频分支的公共实现（`convert_media`）：音频与视频的唯一区别是
「要不要先找同目录的本地字幕」，其余流程（抽音轨统一规格 → 转写 → 渲染）完全一样。
"""

from __future__ import annotations

from pathlib import Path

from core import config as config_mod
from core import paths
from core.progress import NullReporter, ProgressReporter
from l2 import md, subtitle, transcribe
from l2.media import extract_audio, probe_duration
from l2.result import Result


def convert(
    job: dict,
    *,
    cfg: config_mod.Config,
    reporter: ProgressReporter | None = None,
    force: bool = False,
) -> Result:
    """视频 → md。"""
    return convert_media(
        job, cfg=cfg, reporter=reporter, force=force,
        allow_subtitle=True, label="视频",
    )


def convert_media(
    job: dict,
    *,
    cfg: config_mod.Config,
    reporter: ProgressReporter | None = None,
    force: bool = False,
    allow_subtitle: bool,
    label: str = "媒体",
) -> Result:
    """视频 / 音频共用的主流程。"""
    reporter = reporter or NullReporter()
    item_id = str(job["id"])
    media = Path(str(job.get("material") or ""))
    warnings = list(job.get("warnings") or [])

    if not media.exists():
        raise FileNotFoundError(f"{label}素材不存在：{media}")

    # 1) 统一规格音轨（16k 单声道 PCM）。产物落盘复用，重跑不再抽一次
    wav = paths.work_path(item_id, ".wav")
    if force or not wav.exists():
        extract_audio(media, wav, cfg, reporter=reporter)

    segments: list[dict] = []
    extractor = ""

    # 2) 本地字幕优先：命中就完全跳过转写
    if allow_subtitle:
        local = subtitle.find_local_subtitle(media)
        if local is not None:
            segments = subtitle.parse_subtitle(local)
            if segments:
                extractor = f"subtitle:local:{local.suffix.lstrip('.')}"
            else:
                warnings.append(f"本地字幕解析为空，回退转写：{local}")

    # 3) 转写（带分片进度）
    if not segments:
        total = probe_duration(wav, cfg)
        reporter.start(total, f"{label}转写 {media.name}")
        try:
            transcript = transcribe.run(
                wav,
                item_id=item_id,
                cfg=cfg,
                on_progress=lambda done, _total, note: reporter.update(done, note),
                force=force,
            )
        except Exception:
            reporter.close(f"{label}转写失败")
            raise
        segments = transcript.segments
        extractor = transcript.extractor
        warnings += transcript.warnings
        reporter.close(f"转写完成（{extractor}，{len(segments)} 段）")

    text = md.render(
        job, md.transcript_body(segments), extractor=extractor, warnings=warnings
    )
    md_path = md.write(item_id, text)

    return Result(
        item_id=item_id,
        kind=str(job.get("type") or ""),
        md_path=md_path,
        extractor=extractor,
        segment_count=len(segments),
        warnings=warnings,
    )
