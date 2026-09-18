"""L2 分派器：按素材类型走三条分支之一。

进度 reporter 在这里创建，因为「用哪种展示方式」是全局配置，不该由各分支各自决定。
"""

from __future__ import annotations

from core import config as config_mod
from core import paths, progress, route
from l2 import audio, images, video
from l2.result import Result

_BRANCHES = {
    route.VIDEO: video.convert,
    route.AUDIO: audio.convert,
    route.IMAGE_SET: images.convert,
}


class ConvertError(Exception):
    """转换失败。消息面向用户。"""


def convert(
    job: dict,
    *,
    cfg: config_mod.Config | None = None,
    progress_mode: str | None = None,
    force: bool = False,
) -> Result:
    """把一份已登记的素材转成 md。

    Args:
        job:           L1 产出的 job
        progress_mode: inline / window / off；默认取配置
        force:         忽略缓存重算（转写缓存、图片副本、音轨都会重做）
    """
    cfg = cfg or config_mod.get()
    kind = str(job.get("type") or "")
    branch = _BRANCHES.get(kind)
    if branch is None:
        raise ConvertError(
            f"不认识的素材类型：{kind!r}\n  可用：{sorted(_BRANCHES)}"
        )

    item_id = str(job.get("id") or "")
    if not item_id:
        raise ConvertError("job 里没有 id，L1 登记可能未完成。")

    mode = progress_mode or cfg.progress.mode
    reporter = progress.make_reporter(
        mode, progress_path=paths.work_path(item_id, ".progress.json")
    )

    try:
        return branch(job, cfg=cfg, reporter=reporter, force=force)
    except Exception:
        # 告诉进度窗口「已结束」，否则它会一直等到静默超时
        reporter.close("处理失败")
        raise
