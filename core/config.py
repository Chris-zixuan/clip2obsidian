"""配置加载：config.toml + 环境变量覆盖。

设计原则
--------
代码里**不允许**出现硬编码的路径与工具假设（ffmpeg 落点、模型名、收件目录）。
一律从这里取，换机器时只改配置不碰代码。

未知段 / 未知键一律报错 —— 拼错的配置静默失效比直接报错更难查。
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

from core import paths


class ConfigError(Exception):
    """配置有问题时抛出，消息面向用户、可直接展示。"""


# ------------------------------------------------------------------ 各配置段
@dataclass
class IngestConfig:
    """L1 素材登记配置。"""

    # 无参数运行时的收件目录（支持 ~ 与环境变量）
    inbox_dir: str = "~/Downloads/clip2obsidian"

    # `--url` 元信息增强（yt-dlp 只取 JSON、绝不下载）的网络超时
    timeout_sec: int = 60

    # zip 解压后的内容总量上限（MB）。防解压炸弹把磁盘塞满
    max_unzip_mb: int = 4096

    @property
    def inbox(self) -> Path:
        return paths.expand(self.inbox_dir)


@dataclass
class CloudConfig:
    """云端转写：OpenAI 兼容端点（multipart 上传本地音频）。

    可接 OpenAI / 硅基流动 / 小米 MiMo 等。**本次未实测**，
    配置全空时视为不可用，`provider = auto` 会自动走本地。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = "whisper-1"
    timeout_sec: int = 300

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.api_key.strip())


@dataclass
class TranscribeConfig:
    """转写配置。"""

    # auto —— 本地可用就用本地，否则云端；local / cloud 为强制指定
    provider: str = "auto"

    # auto —— 依次探测 mlx-whisper、faster-whisper
    engine: str = "auto"

    # 模型短名。带 "/" 视为完整仓库名直接透传；
    # 否则按引擎各自映射（mlx → mlx-community/whisper-{model}-mlx）
    model: str = "large-v3"

    language: str = "zh"
    initial_prompt: str = ""

    # 强制离线，只用已缓存的模型。换新模型时须临时设为 false 先让它下载
    offline: bool = True

    # 分片时长（秒）。分片是为了拿到确定进度、限制内存、单片刻重试
    chunk_sec: int = 600

    cloud: CloudConfig = field(default_factory=CloudConfig)


@dataclass
class ToolsConfig:
    """外部工具路径。留空则自动探测。"""

    ffmpeg: str = ""
    ffprobe: str = ""

    @property
    def ffmpeg_bin(self) -> str:
        return paths.find_ffmpeg(self.ffmpeg)

    @property
    def ffprobe_bin(self) -> str:
        return paths.find_ffprobe(self.ffprobe)


@dataclass
class ProgressConfig:
    """进度展示方式。"""

    # inline —— 终端就地刷新；window —— 另开终端窗口；off —— 不打进度
    mode: str = "inline"


@dataclass
class Config:
    ingest: IngestConfig = field(default_factory=IngestConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    progress: ProgressConfig = field(default_factory=ProgressConfig)


# ------------------------------------------------------------------ 加载逻辑
_SECTIONS: dict[str, type] = {
    "ingest": IngestConfig,
    "transcribe": TranscribeConfig,
    "tools": ToolsConfig,
    "progress": ProgressConfig,
}

# 环境变量覆盖项：字段路径 -> 环境变量名。便于跨机运行与临时调试。
# 路径用元组表示以支持嵌套段（transcribe.cloud.api_key）。
_ENV_OVERRIDES: dict[tuple[str, ...], str] = {
    ("ingest", "inbox_dir"): "C2O_INBOX_DIR",
    ("transcribe", "provider"): "C2O_TRANSCRIBE_PROVIDER",
    ("transcribe", "engine"): "C2O_TRANSCRIBE_ENGINE",
    ("transcribe", "model"): "C2O_TRANSCRIBE_MODEL",
    ("transcribe", "cloud", "base_url"): "C2O_CLOUD_BASE_URL",
    ("transcribe", "cloud", "api_key"): "C2O_CLOUD_API_KEY",
    ("transcribe", "cloud", "model"): "C2O_CLOUD_MODEL",
    ("tools", "ffmpeg"): "C2O_FFMPEG",
    ("tools", "ffprobe"): "C2O_FFPROBE",
    ("progress", "mode"): "C2O_PROGRESS_MODE",
}

_cache: Config | None = None


def load(config_path: Path | None = None, *, required: bool = True) -> Config:
    """读取配置。

    Args:
        config_path: 显式指定配置文件；默认用项目根的 config.toml
        required:    True 时缺少配置文件直接报错；False 时返回默认值（供自检用）
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
    """按段构造 Config，未知段直接报错。"""
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
    """构造一个配置段。未知键直接报错，嵌套 dataclass 递归处理。"""
    hints = get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"[{section_name}] 里有未知配置项：{sorted(unknown)}\n"
            f"  可用项：{sorted(known)}"
        )

    values: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        want = hints.get(f.name)
        value = data[f.name]
        if dataclasses.is_dataclass(want):
            if not isinstance(value, dict):
                raise ConfigError(f"[{section_name}.{f.name}] 必须是一个配置段")
            values[f.name] = _build_section(f"{section_name}.{f.name}", want, value)
        else:
            values[f.name] = _coerce(section_name, f.name, want, value)
    return cls(**values)


def _coerce(section: str, name: str, want: Any, value: Any) -> Any:
    """按 dataclass 声明的类型做基本校验，把明显写错的配置挡在前面。"""
    if want is int:
        # bool 是 int 的子类，单独挡掉 `chunk_sec = true` 这类写法
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"[{section}].{name} 应为整数，实际是 {value!r}")
    elif want is bool:
        if not isinstance(value, bool):
            raise ConfigError(
                f"[{section}].{name} 应为布尔值（true/false），实际是 {value!r}"
            )
    elif want is str:
        if not isinstance(value, str):
            raise ConfigError(f"[{section}].{name} 应为字符串，实际是 {value!r}")
    return value


def _apply_env(cfg: Config) -> None:
    """环境变量覆盖，便于跨机运行与临时调试。支持嵌套字段路径。"""
    for path, env_name in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if not value:
            continue
        obj: Any = cfg
        for key in path[:-1]:
            obj = getattr(obj, key)
        current = getattr(obj, path[-1])
        if isinstance(current, bool):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            value = int(value)
        setattr(obj, path[-1], value)


def describe(cfg: Config) -> list[tuple[str, str]]:
    """把生效配置整理成 (项, 值) 列表，供自检命令展示。"""
    t = cfg.transcribe
    cloud = "已配置" if t.cloud.configured else "未配置（仅本地）"
    return [
        ("收件目录", str(cfg.ingest.inbox)),
        ("转写 provider", t.provider),
        ("转写引擎", f"{t.engine} / {t.model}" + ("（离线）" if t.offline else "")),
        ("分片时长", f"{t.chunk_sec} 秒"),
        ("云端插槽", cloud),
        ("ffmpeg", cfg.tools.ffmpeg_bin or "(未找到)"),
        ("ffprobe", cfg.tools.ffprobe_bin or "(未找到)"),
        ("进度展示", cfg.progress.mode),
    ]
