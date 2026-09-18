"""音频分支：直接转写 → 纯正文 md。

与视频的唯一差别是不去找同目录的本地字幕 —— 音频旁边基本不会有字幕文件，
而音频本身通常已经是「说完的话」，没有再降级的空间。
"""

from __future__ import annotations

from core import config as config_mod
from core.progress import ProgressReporter
from l2 import video
from l2.result import Result


def convert(
    job: dict,
    *,
    cfg: config_mod.Config,
    reporter: ProgressReporter | None = None,
    force: bool = False,
) -> Result:
    return video.convert_media(
        job, cfg=cfg, reporter=reporter, force=force,
        allow_subtitle=False, label="音频",
    )
