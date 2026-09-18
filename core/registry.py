"""平台路由：把本地文件分派到对应平台的 pipeline。

代码只处理本地文件（不抓链接、不下载），所以入口只有一个：`detect_local(path)`。

新增平台
--------
1. 在 `PLATFORMS` 里加一条 PlatformSpec
2. 新建 `pipelines/{平台}/extract.py`（L2）

L1（本地导入）由 `pipelines/local/ingest.py` 统一完成，无需每平台各写一套。
**不允许**为此修改 publish/ 或 core/schema.py —— 若必须修改，说明抽象失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformSpec:
    """一个平台的注册信息。"""

    name: str
    label: str
    module: str                        # pipelines 包下的模块名
    content_type: str = "video"        # 兜底形态，实际由 extractor 判定
    # 本地文件名特征：命中任一即判为该平台
    name_hints: tuple[str, ...] = ()
    # 文件名里能抠出原生 id 的正则（如 B站的 BV 号）
    id_pattern: str = ""

    def extract_id(self, text: str) -> str:
        """从文本里抠原生 id（B站的 BV 号）；没有则返回空串。

        与 `match_local` 分开：平台判定命中「名字特征」就够，但链接与展示
        需要的是具体 id —— 二者语义不同，混用会导致 BV 号永远抠不出来。
        """
        if self.id_pattern:
            m = re.search(self.id_pattern, text)
            if m:
                return m.group(0)
        return ""

    def match_local(self, name: str) -> str | None:
        """按文件名判定归属，返回抠出的原生 id（可能为 ""）；不属于本平台返回 None。"""
        if native := self.extract_id(name):
            return native
        low = name.lower()
        if any(h.lower() in low for h in self.name_hints):
            return ""
        return None


PLATFORMS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="bilibili",
        label="B站",
        module="bilibili",
        content_type="video",
        name_hints=("哔哩哔哩", "bilibili"),
        id_pattern=r"BV1[0-9A-Za-z]{8,}",
    ),
)


class UnsupportedPlatform(Exception):
    """平台未注册，或本地文件识别不出平台。"""


# 视频扩展名。只有视频形态，不必区分图片目录。
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".flv", ".m4v", ".avi")


def get(name: str) -> PlatformSpec:
    """按平台名取注册信息。"""
    for spec in PLATFORMS:
        if spec.name == name:
            return spec
    raise UnsupportedPlatform(f"未注册的平台名：{name!r}")


def detect_local(path: str | Path) -> PlatformSpec | None:
    """从本地文件推断平台。识别不出返回 None（由调用方提示 --platform）。

    按扩展名锁定「视频」大类，再按文件名特征匹配到具体平台。
    不要求文件真实存在——CLI 里敲的路径此时还没校验存在性，但名字已足够分类。
    """
    p = Path(path).expanduser()
    if p.suffix.lower() not in VIDEO_EXT:
        return None

    for spec in PLATFORMS:
        if spec.match_local(p.name) is not None:
            return spec
    return None
