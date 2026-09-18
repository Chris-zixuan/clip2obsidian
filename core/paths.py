"""路径与平台假设的集中管理。

设计原则
--------
所有平台相关的绝对路径只在本文件出现一次。扩展 Windows 时只需在此补分支，
其余代码不动。

本项目的边界：**只读写项目内的 raw/ 与 work/**，不出现任何知识库路径 ——
入库由配套 skill 与 agent 完成（见 README）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 项目内路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"          # L1 素材登记
WORK_DIR = PROJECT_ROOT / "work"        # L2 产物（md / 音频 / 转写缓存 / 图片副本）
SKILLS_DIR = PROJECT_ROOT / "skills"    # 配套 skill
CONFIG_PATH = PROJECT_ROOT / "config.toml"

# ---------------------------------------------------------------- 运行平台
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 平台标识，用于日志与分支
PLATFORM_TAG = "windows" if IS_WINDOWS else ("macos" if IS_MACOS else sys.platform)

# ---------------------------------------------------------------- 工具探测
# 各平台常见落点，仅作配置缺失时的兜底探测
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
        可执行文件路径；找不到返回空字符串（由调用方决定是否报错）。
    """
    return _find_tool(configured, "ffmpeg", _FFMPEG_CANDIDATES)


def find_ffprobe(configured: str = "") -> str:
    """定位 ffprobe。优先级：显式配置 → PATH → 与 ffmpeg 同目录 → 平台落点。

    ffprobe 与 ffmpeg 通常同目录安装，所以「跟着 ffmpeg 找」是可靠兜底。
    """
    found = _find_tool(configured, "ffprobe", None)
    if found:
        return found

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if IS_WINDOWS else "ffprobe")
        if sibling.exists():
            return str(sibling)
    return ""


def _find_tool(configured: str, name: str, candidates: dict[str, list[str]] | None) -> str:
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
    if found := shutil.which(name):
        return found
    for cand in (candidates or {}).get(sys.platform, []):
        if Path(cand).exists():
            return cand
    return ""


# ---------------------------------------------------------------- 目录管理
def ensure_dirs() -> None:
    """确保项目内的缓存目录存在。"""
    for d in (RAW_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def raw_dir(item_id: str) -> Path:
    """单个条目的素材目录，如 raw/1a2b3c4d5e6f/。"""
    d = RAW_DIR / _dir_name(item_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir(item_id: str) -> Path:
    """单个条目的产物目录，如 work/1a2b3c4d5e6f/（图片副本等）。"""
    d = WORK_DIR / _dir_name(item_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_path(item_id: str, suffix: str) -> Path:
    """单个条目的产物文件，如 work/1a2b3c4d5e6f.md / .asr.json。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR / f"{_dir_name(item_id)}{suffix}"


def _dir_name(item_id: str) -> str:
    """把条目 id 转成适合做目录名的形式（id 里不该有路径分隔符，这里兜一层）。"""
    return item_id.replace(":", "_").replace("/", "_")


def expand(path: str | Path) -> Path:
    """展开 ~ 与环境变量，返回绝对路径。跨平台安全。"""
    return Path(os.path.expandvars(str(path))).expanduser().resolve()
