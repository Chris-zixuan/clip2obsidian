"""路径与平台假设的集中管理。

设计原则
--------
所有平台相关的绝对路径只在本文件出现一次。未来扩展 Windows 时，
只需在此处补分支，其余代码不动（见 docs/架构设计 §6）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 项目内路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"          # L1 原始物料缓存
WORK_DIR = PROJECT_ROOT / "work"        # L2/L3 中间产物
SKILLS_DIR = PROJECT_ROOT / "skills"
CONFIG_PATH = PROJECT_ROOT / "config.toml"
# 同名笔记替换前的备份落点。使用者的真实备份区，测试必须重定向（见 tests/conftest.py）。
BACKUP_DIR = PROJECT_ROOT / ".workbuddy" / "backups"

# ---------------------------------------------------------------- 运行平台
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 平台标识，用于日志与未来分支
PLATFORM_TAG = "windows" if IS_WINDOWS else ("macos" if IS_MACOS else sys.platform)

# ---------------------------------------------------------------- 工具探测
# 各平台 ffmpeg 的常见落点，仅作配置缺失时的兜底探测
_FFMPEG_CANDIDATES: dict[str, list[str]] = {
    "darwin": ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"],
    "win32": [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ],
    "linux": ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"],
}


def find_ffmpeg(configured: str = "") -> str:
    """定位 ffmpeg。优先级：显式配置 → PATH → 平台常见落点。

    Returns:
        ffmpeg 可执行文件路径；找不到时返回空字符串（由调用方决定是否报错）。
    """
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in _FFMPEG_CANDIDATES.get(sys.platform, []):
        if Path(cand).exists():
            return cand
    return ""


def find_python(configured: str = "") -> str:
    """定位 Python 解释器。留空则使用当前解释器。"""
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
    return sys.executable


# ---------------------------------------------------------------- 目录管理
def ensure_dirs() -> None:
    """确保项目内的缓存目录存在。"""
    for d in (RAW_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def raw_dir(clip_id: str) -> Path:
    """单个 clip 的原始物料目录，如 raw/bilibili_BV1xx411c7mD/。"""
    d = RAW_DIR / _dir_name(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_path(clip_id: str, suffix: str) -> Path:
    """单个 clip 的中间产物路径，如 work/bilibili_BV1xx411c7mD.clip.json。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR / f"{_dir_name(clip_id)}{suffix}"


def _dir_name(clip_id: str) -> str:
    """把 clip_id（bilibili:BV1xx）转成适合做目录名的形式（bilibili_BV1xx）。"""
    return clip_id.replace(":", "_")


def expand(path: str | Path) -> Path:
    """展开 ~ 与环境变量，返回绝对路径。跨平台安全。"""
    return Path(os.path.expandvars(str(path))).expanduser().resolve()
