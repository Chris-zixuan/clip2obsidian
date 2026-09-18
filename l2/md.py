"""md 组装：来源块 + 正文。

两条硬规矩
----------
1. **正文里不写 frontmatter**：属性由配套 skill 组装，本项目不碰知识库格式。
2. **元信息用一段 HTML 注释交付**：渲染后不可见（不污染笔记）、人可读、agent 解析完
   直接删掉即可 —— 比再落一个 sidecar 文件少一次文件对账。

图片来源同理：每张图用 `<!--img: 绝对路径 -->` 占位，agent 按路径读图后补正文。
"""

from __future__ import annotations

from pathlib import Path

from core.textnorm import hhmmss, mmss, to_paragraphs

META_OPEN = "<!--c2o:meta"
META_CLOSE = "-->"

# 平台 + 类型 → 剪藏类型值（与知识库里既有的 zhihu-answer / bilibili-video 命名风格一致）
_PLATFORM_TYPES: dict[str, dict[str, str]] = {
    "bilibili": {"video": "bilibili-video"},
    "douyin": {"video": "douyin-video"},
    "xiaohongshu": {"image_set": "xiaohongshu-note"},
}
_KIND_SUFFIX = {"video": "video", "audio": "audio", "image_set": "note"}


def clipping_type(job: dict) -> str:
    """推导剪藏类型：平台 + 素材类型。认不出平台时用 `local-*`。"""
    platform = str(job.get("platform") or "").strip()
    kind = str(job.get("type") or "").strip()
    if (mapped := _PLATFORM_TYPES.get(platform, {}).get(kind)):
        return mapped
    suffix = _KIND_SUFFIX.get(kind, kind or "item")
    return f"{platform or 'local'}-{suffix}"


def source_block(job: dict, *, extractor: str = "") -> str:
    """给入库 agent 读的来源块。空值不写行，绝不写 `unknown` 这类脏值。"""
    meta = job.get("meta") or {}
    rows = [
        ("title", meta.get("title")),
        ("source_url", meta.get("source_url")),
        ("author", meta.get("author")),
        ("published", meta.get("published")),
        ("description", meta.get("description")),
        ("clipping_type", clipping_type(job)),
        ("platform", job.get("platform")),
        ("native_id", job.get("native_id")),
        ("extractor", extractor),
    ]
    lines = [META_OPEN]
    for key, value in rows:
        text = str(value or "").strip().replace("\n", " ")
        if text:
            lines.append(f"{key}: {text}")
    lines.append(META_CLOSE)
    return "\n".join(lines)


def transcript_body(segments: list[dict], *, heading: str = "转写全文") -> str:
    """把碎片转写合并成段落，并按时间戳标注。"""
    paras = to_paragraphs([
        {"start": s.get("start", 0.0), "text": s.get("text", "")} for s in segments
    ])
    lines = [f"## {heading}", ""]
    if not paras:
        lines.append("_（没有识别到内容）_")
        return "\n".join(lines) + "\n"

    for para in paras:
        start = float(para.get("start") or 0.0)
        stamp = mmss(start) if start < 3600 else hhmmss(start)
        lines += [f"**[{stamp}]** {para['text']}", ""]
    return "\n".join(lines).rstrip() + "\n"


def image_body(images: list, *, heading: str = "图片") -> str:
    """图文骨架：每张图一个占位注释 + 待补说明。"""
    lines = [
        f"## {heading}",
        "",
        "> 以下每张图等待 agent 读取后补写文字说明。",
        "",
    ]
    for index, path in enumerate(images, 1):
        lines += [f"<!--img: {path}-->", f"**图 {index}**：", ""]
    return "\n".join(lines).rstrip() + "\n"


def render(job: dict, body: str, *, extractor: str = "", warnings: list[str] | None = None) -> str:
    """组装完整 md：来源块 + 警告 + 正文。"""
    parts = [source_block(job, extractor=extractor), ""]

    alerts = [w for w in (warnings or []) if w.strip()]
    if alerts:
        parts.append(f"> [!warning] {alerts[0]}")
        parts += [f"> {w}" for w in alerts[1:]]
        parts.append("")

    parts.append(body.rstrip())
    return "\n".join(parts).rstrip() + "\n"


def write(item_id: str, text: str) -> Path:
    """写到 `work/{id}.md`。"""
    from core import paths

    path = paths.work_path(item_id, ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
