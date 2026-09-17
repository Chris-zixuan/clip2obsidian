"""clip.json —— 四层之间唯一的通信契约。

设计要点
--------
1. 所有平台的 extractor 都产出这个结构，distill 与 publish 只认它。
   新增平台时只要能填满这份结构，下游三层零改动。
2. 三种内容形态（video / image_text / article）共用一套字段，
   下游不必写 if platform == ... 的分支。
3. `provenance` 记录抓取手段与版本 —— 笔记里那句「未经校对」提示
   由此自动生成，而不是靠人记得写。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# 已知平台与内容形态。新增平台时在这里补一个值即可（配合 core/registry.py）。
KNOWN_PLATFORMS = ("douyin", "xiaohongshu")
CONTENT_TYPES = ("video", "image_text", "article")
ASSET_KINDS = ("image", "video", "cover", "audio", "subtitle")
ASSET_ROLES = ("cover", "content")
OCR_STATUS = ("pending", "done", "failed")


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
    """下载到本地的媒体文件。

    `kind` / `path` 给默认空串而非设为必填：这样坏数据能读进来、由
    `validate()` 报成可读问题，而不是在 `from_dict` 阶段抛 TypeError 栈。
    """

    kind: str = ""               # 见 ASSET_KINDS
    path: str = ""               # 相对项目根，如 raw/douyin_123/01.jpg
    order: int = 0               # 正文中的排列顺序，从 1 开始；封面为 0
    role: str = "content"        # 见 ASSET_ROLES


@dataclass
class TextBlock:
    """线性文本块：正文段落、图内文字等。"""

    type: str = "paragraph"      # paragraph | heading | list | quote
    text: str = ""


@dataclass
class Segment:
    """时间轴转写片段。"""

    start: float = 0.0
    end: float = 0.0
    text: str = ""


@dataclass
class ImageOcr:
    """单张图片的内容转写（由 agent 读图后回填）。

    status: pending 表示尚未回填 —— 这时 clip.json 是不完整的，
    publish 层应当拒绝入库。
    """

    order: int = 0
    text: str = ""
    status: str = "pending"


@dataclass
class Content:
    """内容主体，三分区。"""

    text_blocks: list[TextBlock] = field(default_factory=list)
    transcript: list[Segment] = field(default_factory=list)   # 仅 video
    images_ocr: list[ImageOcr] = field(default_factory=list)  # 仅 image_text


@dataclass
class Provenance:
    """来源追踪：谁抓的、怎么提的、有什么已知问题。"""

    fetcher: str = ""            # 如 yt-dlp@2026.09.15
    extractor: str = ""          # 如 subtitle:auto / asr:faster-medium / vision:agent
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
        # 兼容 topics/stats 被写成字符串等脏数据的情况
        if not isinstance(meta.topics, list):
            meta.topics = [str(meta.topics)]

        content_d = d.get("content") or {}
        content = Content(
            text_blocks=[TextBlock(**_pick(TextBlock, x)) for x in content_d.get("text_blocks") or []],
            transcript=[Segment(**_pick(Segment, x)) for x in content_d.get("transcript") or []],
            images_ocr=[ImageOcr(**_pick(ImageOcr, x)) for x in content_d.get("images_ocr") or []],
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

    @property
    def content_assets(self) -> list[Asset]:
        """正文媒体（不含封面）。"""
        return [a for a in self.assets if a.role == "content"]

    @property
    def cover(self) -> Asset | None:
        for a in self.assets:
            if a.role == "cover" or a.kind == "cover":
                return a
        return None

    def full_text(self, *, include_transcript: bool = True) -> str:
        """拼出可用于摘要、description、字数统计的纯文本。"""
        parts: list[str] = [b.text for b in self.content.text_blocks if b.text]
        if include_transcript:
            parts.extend(s.text for s in self.content.transcript if s.text)
        parts.extend(o.text for o in self.content.images_ocr if o.text)
        return "\n".join(p for p in parts if p).strip()

    def pending_ocr(self) -> list[ImageOcr]:
        """尚未回填的图片转写项。"""
        return [o for o in self.content.images_ocr if o.status != "done"]

    def ocr_ready(self) -> bool:
        """image_text 类型是否已完成图片内容回填。"""
        if self.content_type != "image_text":
            return True
        if not self.content.images_ocr:
            return False
        return not self.pending_ocr()

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
        if not self.source_url:
            problems.append("source_url 为空")

        for a in self.assets:
            if a.kind not in ASSET_KINDS:
                problems.append(f"asset.kind 非法：{a.kind!r}")
            if a.role not in ASSET_ROLES:
                problems.append(f"asset.role 非法：{a.role!r}")
            if not a.path:
                problems.append("asset.path 为空")

        for o in self.content.images_ocr:
            if o.status not in OCR_STATUS:
                problems.append(f"images_ocr.status 非法：{o.status!r}")

        # 内容形态与内容负载的对应关系
        if self.content_type == "video" and not self.content.transcript:
            problems.append("video 类型但 transcript 为空（应至少有一条转写或字幕）")
        if self.content_type == "image_text":
            imgs = [a for a in self.content_assets if a.kind == "image"]
            if imgs and not self.content.images_ocr:
                problems.append(
                    f"image_text 有 {len(imgs)} 张正文图但 images_ocr 为空"
                    "（需 agent 读图回填）"
                )
        if not self.full_text() and not self.content_assets:
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
