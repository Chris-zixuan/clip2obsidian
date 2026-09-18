"""转写 provider 协议：本地优先，云端可插拔。

为什么要有这一层
----------------
用户的要求是「本地能用就用本地，效果不好再换云服务商」。若把付费 API 写死在主链路，
换厂商要动核心代码；抽象成 provider 后，换厂商只是加一个文件加一段配置。

选择规则（`provider = auto`）
---------------------------
本地可用就用本地（免费、离线、时间戳完整、无时长限制）→ 否则用云端。
两者都不可用时**报错并同时给出两条路的原因**，而不是静默产出一个空转写。
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from core import config as config_mod
from core import paths

# 自动转写必须让使用者知道：这份文本没有经过人工校对
WARN_ASR = "本文为自动转写，未经人工校对，同音字 / 专有名词可能存在误差"

# on_progress(已处理秒, 总秒, 说明)
ProgressCb = Callable[[float, float, str], None]


class TranscribeError(Exception):
    """转写失败。消息面向用户、带可执行的下一步建议。"""


@dataclass
class Transcript:
    """统一的转写结果。两种本地引擎与云端 provider 都产出这个结构。"""

    segments: list[dict] = field(default_factory=list)   # [{"start","end","text"}]
    extractor: str = ""                                  # 如 whisper:mlx:large-v3
    language: str = ""
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(s.get("text", "") for s in self.segments if s.get("text"))

    def to_dict(self) -> dict:
        return {
            "extractor": self.extractor,
            "language": self.language,
            "duration": self.duration,
            "segments": self.segments,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        return cls(
            segments=list(data.get("segments") or []),
            extractor=str(data.get("extractor") or ""),
            language=str(data.get("language") or ""),
            duration=float(data.get("duration") or 0.0),
            warnings=list(data.get("warnings") or []),
        )


class Provider(Protocol):
    """转写实现。"""

    name: str

    def available(self, cfg: config_mod.Config) -> tuple[bool, str]:
        """返回 (是否可用, 不可用原因)。原因会在降级提示里展示给用户。"""
        ...

    def transcribe(
        self,
        audio: Path,
        *,
        cfg: config_mod.Config,
        on_progress: ProgressCb | None = None,
    ) -> Transcript:
        ...


# provider 名 -> 模块路径。新增厂商只需加一个模块并在这里注册
_PROVIDER_MODULES: dict[str, str] = {
    "local": "l2.transcribe.local",
    "cloud": "l2.transcribe.openai_compat",
}


def get_provider(name: str) -> Provider:
    """按名字取 provider 实例（模块级单例，惰性导入）。"""
    module_path = _PROVIDER_MODULES.get(name)
    if module_path is None:
        raise TranscribeError(
            f"未知的转写 provider：{name!r}\n"
            f"  可选：{sorted(_PROVIDER_MODULES)}（或用 auto 自动选择）"
        )
    module = importlib.import_module(module_path)
    return module.PROVIDER


def resolve_provider(cfg: config_mod.Config) -> tuple[Provider, list[str]]:
    """挑一个可用的 provider。

    Returns:
        (provider, warnings)。warnings 用于说明「为什么没用你指定的那个」。
    """
    want = (cfg.transcribe.provider or "auto").strip().lower()
    if want == "auto":
        order = ["local", "cloud"]
    elif want in _PROVIDER_MODULES:
        order = [want]
    else:
        raise TranscribeError(
            f"未知的 provider：{want!r}\n"
            f"  可选：auto（默认，本地优先）/ {' / '.join(sorted(_PROVIDER_MODULES))}"
        )

    reasons: list[str] = []
    for index, name in enumerate(order):
        provider = get_provider(name)
        ok, why = provider.available(cfg)
        if ok:
            warnings = []
            if index > 0 and want == "auto":
                warnings.append(
                    f"本地转写不可用（{reasons[0]}），已改用云端 provider。"
                )
            return provider, warnings
        reasons.append(f"{name}：{why}")

    raise TranscribeError(
        "没有可用的转写实现：\n  - " + "\n  - ".join(reasons) + "\n"
        "  本地：pip install mlx-whisper（Apple Silicon）或 faster-whisper\n"
        "  云端：在 config.toml 的 [transcribe.cloud] 填 base_url 与 api_key"
    )


def run(
    audio: Path,
    *,
    item_id: str,
    cfg: config_mod.Config,
    on_progress: ProgressCb | None = None,
    force: bool = False,
) -> Transcript:
    """带缓存的转写入口。

    缓存命中就完全不碰引擎 —— 转写是整条链路最贵的一步（时间或钱），
    重跑流程时不该重复付出。
    """
    cache = paths.work_path(item_id, ".asr.json")
    if cache.exists() and not force:
        try:
            cached = Transcript.from_dict(json.loads(cache.read_text(encoding="utf-8")))
            if cached.segments:
                return cached
        except (json.JSONDecodeError, OSError, ValueError):
            pass   # 缓存坏了当作没有，重新转写

    provider, warnings = resolve_provider(cfg)
    result = provider.transcribe(Path(audio), cfg=cfg, on_progress=on_progress)
    result.warnings = [*warnings, *result.warnings]

    if result.segments:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result
