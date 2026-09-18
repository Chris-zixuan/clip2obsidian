"""ASR 引擎统一接口。

新增引擎：在本目录加一个模块（如 `whisper_api.py`），暴露一个 `Engine` 类，
然后在 `_ENGINE_MODULES` 注册即可。上层代码不需要任何改动。

注意命名：本项目一律用 **ASR**（语音转文字），不用 TTS —— TTS 是反向的
「文字转语音」。
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class AsrError(Exception):
    """转写失败。消息面向用户。"""


@dataclass
class AsrResult:
    """转写结果。所有引擎都产出这个结构。"""

    segments: list[dict] = field(default_factory=list)   # [{"start","end","text"}]
    text: str = ""
    language: str = ""
    duration: float = 0.0
    backend: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "language": self.language,
            "duration": self.duration,
            "text": self.text,
            "segments": self.segments,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AsrResult":
        return cls(
            segments=d.get("segments") or [],
            text=d.get("text") or "",
            language=d.get("language") or "",
            duration=float(d.get("duration") or 0.0),
            backend=d.get("backend") or "",
            model=d.get("model") or "",
        )

    @property
    def is_empty(self) -> bool:
        return not self.segments


class AsrEngine(ABC):
    """转写引擎基类。"""

    name: str = ""

    @abstractmethod
    def transcribe(
        self,
        audio: Path,
        *,
        model: str = "medium",
        language: str = "zh",
        initial_prompt: str = "",
    ) -> AsrResult:
        """把音频文件转成文本。"""


# 引擎名 → 模块路径。新增引擎：加一个模块并在这里注册，上层零改动。
_ENGINE_MODULES: dict[str, str] = {
    "faster": "asr.faster",
}


def available() -> list[str]:
    return sorted(_ENGINE_MODULES)


def get_engine(name: str) -> AsrEngine:
    """按名字取引擎实例。"""
    key = (name or "").strip().lower()
    if key not in _ENGINE_MODULES:
        raise AsrError(
            f"未知的转写引擎 {name!r}。可选：{available()}\n"
            f"  在 config.toml 的 [asr].backend 里配置。"
        )
    module = importlib.import_module(_ENGINE_MODULES[key])
    return module.Engine()
