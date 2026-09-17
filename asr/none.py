"""占位引擎：不做转写。

用途：只想用平台自带字幕、不希望跑 ASR 的场景（字幕命中时秒出）。
若字幕未命中，此引擎会让流程明确失败，而不是悄悄降级成空内容。
"""

from __future__ import annotations

from pathlib import Path

from asr.base import AsrEngine, AsrError, AsrResult


class Engine(AsrEngine):
    name = "none"

    def transcribe(
        self,
        audio: Path,
        *,
        model: str = "",
        language: str = "zh",
        initial_prompt: str = "",
    ) -> AsrResult:
        raise AsrError(
            "[asr].backend = \"none\"，按配置不执行转写，但本次未命中平台字幕。\n"
            "  要么把 backend 改成 faster 或 mlx，要么换一条有字幕的内容。"
        )
