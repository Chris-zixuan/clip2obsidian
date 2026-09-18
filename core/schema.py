"""clip.json —— 四层之间唯一的通信契约。

设计要点
--------
1. 所有平台的 extractor 都产出这个结构，distill 与 publish 只认它。
   新增平台时只要能填满这份结构，下游零改动。
2. `provenance` 记录提取手段与已知问题 —— 笔记里那句「未经校对」提示
   由此自动生成，而不是靠人记得写。

本地导入场景下 `source_url` 允许为空（用户只给了本地文件，没有链接）。
空值在渲染层优雅降级，不产出 `[[unknown]]` 这类脏值。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# 已知平台与内容形态。新增平台时在 core/registry.py 同步注册。
KNOWN_PLATFORMS = ("bilibili",)
CONTENT_TYPES = ("video",)
ASSET_KINDS = ("video", "cover")


def _pick(cls: type, data: Any) -> dict:
    """只取 dataclass 声明过的字段，未知键静默忽略（向前兼容）。"""
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in names}


# ------------------------------------------------------------------ 子结构
@dataclass
class Stats:
    """互动数据。缺失一律填 0，不用 None（避免下游到处判空）。"""

    like: int = 0
    collect: int = 0
    comment: int = 0
    share: int = 0


@dataclass
class Meta:
    """作品元信息。"""

    title: str = ""
    author: str = ""
    author_id: str = ""
    published: str = ""          # YYYY-MM-DD；拿不到就留空串
    duration_sec: int = 0
    stats: Stats = field(default_factory=Stats)
    topics: list[str] = field(default_factory=list)   # 平台话题，非知识库标签
    description: str = ""


@dataclass
class Asset:
    """本地媒体文件。

    `kind` / `path` 给默认空串而非设为必填：这样坏数据能读进来、由
    `validate()` 报成可读问题，而不是在 `from_dict` 阶段抛 TypeError 栈。
    """

    kind: str = ""               # 见 ASSET_KINDS
    path: str = ""               # 本地绝对路径
    order: int = 0


@dataclass
class Segment:
    """时间轴转写片段。"""

    start: float = 0.0
    end: float = 0.0
    text: str = ""


@dataclass
class Content:
    """内容主体。"""

    transcript: list[Segment] = field(default_factory=list)


@dataclass
class Provenance:
    """来源追踪：谁导入的、怎么提的、有什么已知问题。"""

    fetcher: str = ""            # 如 local-import
    extractor: str = ""          # 如 subtitle:local:srt / asr:faster:medium
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ 主结构
@dataclass
class Clip:
    """一条收藏的完整中间产物。"""

    id: str                                  # {platform}:{原生id}
    platform: str
    content_type: str
    source_url: str
    canonical_url: str = ""
    fetched_at: str = ""                     # ISO 8601 带时区
    schema_version: str = SCHEMA_VERSION
    meta: Meta = field(default_factory=Meta)
    assets: list[Asset] = field(default_factory=list)
    content: Content = field(default_factory=Content)
    provenance: Provenance = field(default_factory=Provenance)

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        meta_d = d.get("meta") or {}
        # stats 需要单独构造，先从扁平字段里摘出去，否则会重复传参
        meta_fields = _pick(Meta, meta_d)
        meta_fields.pop("stats", None)
        meta = Meta(**meta_fields, stats=Stats(**_pick(Stats, meta_d.get("stats"))))
        # 兼容 topics 被写成字符串等脏数据的情况
        if not isinstance(meta.topics, list):
            meta.topics = [str(meta.topics)]

        content_d = d.get("content") or {}
        content = Content(
            transcript=[Segment(**_pick(Segment, x)) for x in content_d.get("transcript") or []],
        )

        # 嵌套字段单独构造，先从顶层扁平字段里摘出去，否则会重复传参
        nested = ("meta", "assets", "content", "provenance")
        top = {k: v for k, v in _pick(cls, d).items() if k not in nested}

        # 必填字段缺失时补空值，交给 validate() 报出可读问题。
        # 不补的话这里是 TypeError 栈，而 `clip.py` 只会打出一行晦涩的
        # "missing required positional argument"，用户看不出是文件坏了。
        for required in ("id", "platform", "content_type", "source_url"):
            top.setdefault(required, "")

        return cls(
            **top,
            meta=meta,
            assets=[Asset(**_pick(Asset, x)) for x in d.get("assets") or []],
            content=content,
            provenance=Provenance(**_pick(Provenance, d.get("provenance") or {})),
        )

    @classmethod
    def load(cls, path: Path) -> "Clip":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---------------------------------------------------------- 便捷属性
    @property
    def platform_id(self) -> str:
        """原生 id（去掉平台前缀）。"""
        return self.id.split(":", 1)[1] if ":" in self.id else self.id

    def full_text(self) -> str:
        """拼出可用于摘要、description、字数统计的纯文本。"""
        return "\n".join(s.text for s in self.content.transcript if s.text).strip()

    # ---------------------------------------------------------- 自校验
    def validate(self) -> list[str]:
        """返回问题列表；空列表表示通过。

        这里只校验**结构完整性**，不校验业务质量。
        """
        problems: list[str] = []

        if self.schema_version != SCHEMA_VERSION:
            problems.append(
                f"schema_version 为 {self.schema_version}，当前支持 {SCHEMA_VERSION}"
            )
        if ":" not in self.id:
            problems.append(f"id 应为 '平台:原生id' 形式，实际是 {self.id!r}")
        elif self.platform not in self.id.split(":", 1)[0]:
            problems.append(f"id 前缀与 platform 不一致：{self.id!r} / {self.platform!r}")
        if self.platform not in KNOWN_PLATFORMS:
            problems.append(
                f"未知平台 {self.platform!r}，已知：{list(KNOWN_PLATFORMS)}"
            )
        if self.content_type not in CONTENT_TYPES:
            problems.append(
                f"content_type 非法：{self.content_type!r}，可选 {list(CONTENT_TYPES)}"
            )
        # 注：source_url 允许为空（本地导入场景没有链接）。
        # 空链接在渲染层优雅降级，不在此处报结构错误。

        for a in self.assets:
            if a.kind not in ASSET_KINDS:
                problems.append(f"asset.kind 非法：{a.kind!r}")
            if not a.path:
                problems.append("asset.path 为空")

        if self.content_type == "video" and not self.content.transcript:
            problems.append("video 类型但 transcript 为空（应至少有一条转写或字幕）")
        if not self.full_text() and not self.assets:
            problems.append("内容为空：既无文本也无媒体")

        return problems


def new_clip(
    *,
    platform: str,
    platform_id: str,
    content_type: str,
    source_url: str,
    canonical_url: str = "",
    fetched_at: str = "",
) -> Clip:
    """构造一个空 Clip，字段由各平台 extractor 逐步填充。"""
    return Clip(
        id=f"{platform}:{platform_id}",
        platform=platform,
        content_type=content_type,
        source_url=source_url,
        canonical_url=canonical_url or source_url,
        fetched_at=fetched_at,
    )
