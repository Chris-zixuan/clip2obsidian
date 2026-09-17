"""配置加载：config.toml + 环境变量覆盖。

设计原则
--------
代码里**不允许**出现硬编码的平台假设（浏览器名、vault 路径、ffmpeg 路径）。
一律从这里取，这样换机器 / 换浏览器 / 扩展 Windows 时只改配置不碰代码。
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from core import paths


class ConfigError(Exception):
    """配置有问题时抛出，消息面向用户、可直接展示。"""


# ------------------------------------------------------------------ 各配置段
@dataclass
class VaultConfig:
    """知识库位置。"""

    path: str = ""
    inbox_subdir: str = "0_Inbox/Clippings"
    attachment_subdir: str = "8_附件"

    @property
    def root(self) -> Path:
        return paths.expand(self.path)

    @property
    def inbox(self) -> Path:
        return self.root / self.inbox_subdir

    @property
    def attachments(self) -> Path:
        return self.root / self.attachment_subdir


@dataclass
class FetchConfig:
    """L1 采集层配置。"""

    browser: str = "edge"
    timeout_sec: int = 60
    video_format: str = "bytevc1_540p_121262-0/best"
    prefer_subtitle: bool = True

    @property
    def cookie_args(self) -> list[str]:
        """转成 yt-dlp 的 cookie 参数。

        这是「浏览器可配置」这一扩展点的**唯一出口** —— 其他地方不得
        直接拼 `--cookies-from-browser`。
        """
        name = (self.browser or "").strip().lower()
        if name in ("", "none", "off", "no"):
            return []
        return ["--cookies-from-browser", name]


@dataclass
class AsrConfig:
    """L2 转写引擎配置。"""

    backend: str = "faster"
    model: str = "medium"
    language: str = "zh"
    initial_prompt: str = ""
    # 强制离线，只用已缓存的模型。已缓存时能避免联网检查、启动更快；
    # 换新模型（如 medium → large-v3）时须临时设为 false 让它先下载。
    offline: bool = True


@dataclass
class XhsConfig:
    """小红书专有配置。"""

    mobile_ua: str = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    )


@dataclass
class ToolsConfig:
    """外部工具路径。"""

    ffmpeg: str = ""
    python: str = ""

    @property
    def ffmpeg_bin(self) -> str:
        return paths.find_ffmpeg(self.ffmpeg)

    @property
    def python_bin(self) -> str:
        return paths.find_python(self.python)


@dataclass
class PublishConfig:
    """L4 入库层配置。"""

    filename_max_len: int = 60
    verify_after_publish: bool = True

    # frontmatter `description` 的取值口径：
    #   auto       —— 平台自带文案够长就用它，否则退回转写（默认）
    #   platform   —— 只用平台自带文案
    #   transcript —— 只用转写 / 读图文本
    # 默认 auto 的理由见 publish/render.py::_description_source
    description_source: str = "auto"


@dataclass
class Config:
    vault: VaultConfig = field(default_factory=VaultConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    xiaohongshu: XhsConfig = field(default_factory=XhsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)


# ------------------------------------------------------------------ 加载逻辑
_SECTIONS: dict[str, type] = {
    "vault": VaultConfig,
    "fetch": FetchConfig,
    "asr": AsrConfig,
    "xiaohongshu": XhsConfig,
    "tools": ToolsConfig,
    "publish": PublishConfig,
}

# 环境变量覆盖项：{段, 字段} -> 环境变量名。便于临时调试与跨机运行。
_ENV_OVERRIDES: dict[tuple[str, str], str] = {
    ("vault", "path"): "C2O_VAULT_PATH",
    ("vault", "inbox_subdir"): "C2O_INBOX_SUBDIR",
    ("fetch", "browser"): "C2O_BROWSER",
    ("asr", "backend"): "C2O_ASR_BACKEND",
    ("asr", "model"): "C2O_ASR_MODEL",
    ("tools", "ffmpeg"): "C2O_FFMPEG",
}

_cache: Config | None = None


def load(config_path: Path | None = None, *, required: bool = True) -> Config:
    """读取配置。

    Args:
        config_path: 显式指定配置文件；默认用项目根的 config.toml。
        required:    True 时缺少配置文件直接报错；False 时返回默认值（供 doctor 自检用）。
    """
    cfg_path = config_path or paths.CONFIG_PATH
    raw: dict = {}

    if cfg_path.exists():
        try:
            with cfg_path.open("rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"配置文件语法错误：{cfg_path}\n  {e}") from e
    elif required:
        raise ConfigError(
            f"未找到配置文件：{cfg_path}\n"
            f"  请先复制模板：cp config.example.toml config.toml"
        )

    cfg = _build(raw)
    _apply_env(cfg)
    return cfg


def get(*, reload: bool = False) -> Config:
    """取全局配置（带缓存）。"""
    global _cache
    if _cache is None or reload:
        _cache = load()
    return _cache


def _build(raw: dict) -> Config:
    """按段构造 Config，未知键直接报错（防拼写错误静默失效）。"""
    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigError(
            f"配置里有未知的段：{sorted(unknown_sections)}\n"
            f"  可用段：{sorted(_SECTIONS)}"
        )

    kwargs = {}
    for name, cls in _SECTIONS.items():
        section = raw.get(name) or {}
        if not isinstance(section, dict):
            raise ConfigError(f"[{name}] 必须是一个配置段（键值对）")
        kwargs[name] = _build_section(name, cls, section)
    return Config(**kwargs)


def _build_section(section_name: str, cls: type, data: dict):
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"[{section_name}] 里有未知配置项：{sorted(unknown)}\n"
            f"  可用项：{sorted(known)}"
        )

    values = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        values[f.name] = _coerce(section_name, f, data[f.name])
    return cls(**values)


def _coerce(section: str, f: dataclasses.Field, value):
    """按 dataclass 声明的类型做基本校验，把明显写错的配置挡在前面。"""
    want = f.type
    if want is int or want == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"[{section}].{f.name} 应为整数，实际是 {value!r}")
    elif want is bool or want == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"[{section}].{f.name} 应为布尔值（true/false），实际是 {value!r}")
    elif want is str or want == "str":
        if not isinstance(value, str):
            raise ConfigError(f"[{section}].{f.name} 应为字符串，实际是 {value!r}")
    return value


def _apply_env(cfg: Config) -> None:
    """环境变量覆盖，便于跨机运行与临时调试。"""
    for (section, fieldname), env_name in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if not value:
            continue
        section_obj = getattr(cfg, section)
        current = getattr(section_obj, fieldname)
        if isinstance(current, bool):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            value = int(value)
        setattr(section_obj, fieldname, value)


def describe(cfg: Config) -> list[tuple[str, str]]:
    """把生效配置整理成 (项, 值) 列表，供 doctor 展示。"""
    return [
        ("知识库根", str(cfg.vault.root)),
        ("剪藏落点", str(cfg.vault.inbox)),
        ("附件目录", str(cfg.vault.attachments)),
        ("浏览器 cookie 源", cfg.fetch.browser or "(none)"),
        ("字幕优先", "是" if cfg.fetch.prefer_subtitle else "否"),
        ("ASR 引擎", f"{cfg.asr.backend} / {cfg.asr.model}"
                     + ("（离线）" if cfg.asr.offline else "")),
        ("ffmpeg", cfg.tools.ffmpeg_bin or "(未找到)"),
        ("Python", cfg.tools.python_bin),
    ]
