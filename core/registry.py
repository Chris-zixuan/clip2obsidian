"""平台路由：把一条链接（或一段分享文案）分派到对应平台的 pipeline。

新增平台的唯一改动点
--------------------
1. 在 `PLATFORMS` 里加一条 PlatformSpec
2. 新建 pipelines/{平台}/ 目录，实现 fetch / extract
3. 在 CLI 的分派表里注册一行

**不允许**为此修改 publish/ 或 core/schema.py —— 若必须修改，说明抽象失败。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 分享文案里的 URL 匹配。抖音/小红书的分享文本常夹带大量噪声：
#   "3.87 复制打开抖音，看看【不知所的作品】…… https://v.douyin.com/xxx/ d@A.Ty vfo:/ :6pm 07/10"
#   "情侣长途自驾必备清单｜3次长途整理出来的 和对象跑过... https://xhslink.cn/o/UKWULDJKsT 保留口令，直达【小红书】围观~"
_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff<>\"'）】]+", re.I)


@dataclass(frozen=True)
class PlatformSpec:
    """一个平台的注册信息。"""

    name: str
    label: str
    patterns: tuple[str, ...]          # 用于判定 URL 归属的正则
    default_content_type: str          # 兜底形态，实际由 extractor 判定
    module: str                        # pipelines 包下的模块名

    def matches(self, url: str) -> bool:
        return any(re.search(p, url, re.I) for p in self.patterns)


PLATFORMS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="douyin",
        label="抖音",
        patterns=(
            r"v\.douyin\.com/",
            r"douyin\.com/(?:video|note|share/video|share/note)/",
            r"iesdouyin\.com/",
        ),
        default_content_type="video",
        module="douyin",
    ),
    PlatformSpec(
        name="xiaohongshu",
        label="小红书",
        patterns=(
            r"xhslink\.(?:cn|com)/",
            r"xiaohongshu\.com/(?:discovery/item|explore)/",
        ),
        default_content_type="image_text",
        module="xiaohongshu",
    ),
)


class UnsupportedLink(Exception):
    """链接不属于任何已注册平台。"""


def extract_urls(text: str) -> list[str]:
    """从分享文案里抠出所有链接（去重保序，去掉尾部标点）。"""
    found = _URL_RE.findall(text or "")
    cleaned: list[str] = []
    for u in found:
        u = u.rstrip(".,;:!?，。；：！？、")
        if u not in cleaned:
            cleaned.append(u)
    return cleaned


def extract_url(text: str) -> str:
    """从分享文案里抠出第一个链接。"""
    urls = extract_urls(text)
    if not urls:
        raise UnsupportedLink(
            "没有从输入里找到链接。请粘贴完整分享文案或直接粘贴 URL。"
        )
    return urls[0]


def detect(url: str) -> PlatformSpec:
    """判定链接属于哪个平台。"""
    for spec in PLATFORMS:
        if spec.matches(url):
            return spec
    supported = "、".join(f"{p.label}({p.name})" for p in PLATFORMS)
    raise UnsupportedLink(
        f"暂不支持这个链接：{url}\n"
        f"  当前已注册平台：{supported}\n"
        f"  新增平台见 core/registry.py 顶部说明。"
    )


def get(name: str) -> PlatformSpec:
    """按平台名取注册信息。"""
    for spec in PLATFORMS:
        if spec.name == name:
            return spec
    raise UnsupportedLink(f"未注册的平台名：{name!r}")
