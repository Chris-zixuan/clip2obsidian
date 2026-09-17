"""入库层（L4）：clip.json → 符合知识库规范的 Obsidian 笔记。

职责边界
--------
- **代码负责**：文件名清洗、frontmatter 组装、正文结构、附件落位、断链校验。
  这些是机械劳动，代码做才能保证每篇完全一致。
- **agent 负责**：标题精简（抖音标题常被截断）、知识库标签选择（需语义判断）。
  通过参数传入，不在本层做判断。

落库口径（2026-09-17 用户拍板，见架构设计 §9 决策 2A / 3A）
----------------------------------------------------------
- 落点：`0_Inbox/Clippings/`（协作约定 §3.2）
- `类型: clippings`；平台差异用 `clipping_type` 表达，不建平台子目录
- 来源层字段（`title` / `source` / `author` / `published` / `created` /
  `clipping_type` / `description`）由本工具写入 —— 视同 importer 产物，
  字段名与值和既有剪藏保持一致
- `tags` 只用受控词表标签，**不带** `clippings`（协作约定 §4.4）
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core import config as config_mod
from core import paths, schema
from core.textnorm import hhmmss, mmss, truncate

# 文件名非法字符
_ILLEGAL = r'[\\/:*?"<>|#\[\]]'
# 平台 → clipping_type 值（与库内既有 zhihu-answer 命名风格一致）
CLIPPING_TYPES = {
    "douyin": "douyin-video",
    "xiaohongshu": "xiaohongshu-note",
}


class PublishError(Exception):
    """入库失败。消息面向用户。"""


@dataclass
class PublishResult:
    path: Path
    note_name: str
    assets: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    replaced_backup: Path | None = None


# ------------------------------------------------------------------ 主流程
def render(
    clip: schema.Clip,
    *,
    tags: list[str],
    digest: str = "",
    title: str = "",
    cfg: config_mod.Config | None = None,
    dry_run: bool = False,
) -> PublishResult:
    """把 Clip 渲染成 vault 笔记。

    Args:
        tags:   知识库受控标签（agent 决定，§4.3 要求至少 1 个）
        digest: 摘要正文（digest.md 内容，agent 产出）
        title:  标题覆写（agent 精简后的标题），留空则用 clip.meta.title
    """
    cfg = cfg or config_mod.get()

    problems = clip.validate()
    if problems:
        raise PublishError(
            "clip.json 结构校验未通过，拒绝入库：\n  - " + "\n  - ".join(problems)
        )
    if not clip.ocr_ready():
        raise PublishError(
            "image_text 类型还有图片内容未回填（images_ocr.status = pending）。\n"
            "  请先让 agent 读图并回填 work/*.clip.json，再入库。"
        )
    if not tags:
        raise PublishError(
            "没有指定知识库标签。协作约定 §4.3 要求每篇至少 1 个受控词表标签。\n"
            "  用法：--tags 生活,成长"
        )

    note_title = _clean_title(title or clip.meta.title or clip.platform_id)
    note_name = _sanitize(note_title, cfg.publish.filename_max_len)
    outdir = cfg.vault.inbox
    note_path = outdir / f"{note_name}.md"

    result = PublishResult(path=note_path, note_name=note_name)

    # 附件落位（图片类内容才有）
    body_assets = _place_assets(clip, note_name, cfg, dry_run=dry_run, result=result)

    if dry_run:
        return result

    outdir.mkdir(parents=True, exist_ok=True)
    if note_path.exists():
        result.replaced_backup = _backup(note_path)
        result.warnings.append(f"同名笔记已存在，旧版已备份到 {result.replaced_backup}")

    content = _compose(clip, note_title, tags, digest, body_assets, cfg)
    note_path.write_text(content, encoding="utf-8")

    if cfg.publish.verify_after_publish:
        result.warnings.extend(_verify(cfg, note_name))
    return result


# ------------------------------------------------------------------ 组装
def _compose(
    clip: schema.Clip,
    title: str,
    tags: list[str],
    digest: str,
    body_assets: list[tuple[schema.Asset, str]],
    cfg: config_mod.Config,
) -> str:
    fm = _frontmatter(clip, title, tags, cfg)
    info = _info_callout(clip)
    parts = [fm, "", info]

    if digest.strip():
        parts += ["", digest.strip()]

    warn = _warning_callout(clip)
    if warn:
        parts += ["", warn]

    if clip.content_type == "video":
        parts += _body_video(clip)
    else:
        parts += _body_rich(clip, body_assets)

    return "\n".join(parts).rstrip() + "\n"


def _frontmatter(clip: schema.Clip, title: str, tags: list[str], cfg: config_mod.Config) -> str:
    """来源层字段与既有剪藏保持一致（用户拍板 2A）。"""
    author = clip.meta.author or "unknown"
    source = clip.canonical_url or clip.source_url
    clip_type = CLIPPING_TYPES.get(clip.platform, f"{clip.platform}-clip")
    desc = truncate(
        _description_source(clip, cfg.publish.description_source), 120
    )

    lines = [
        "---",
        "类型: clippings",
        f'title: "{_yaml_escape(title)}"',
        f'source: "{_yaml_escape(source)}"',
        "author:",
        f'  - "[[{_yaml_escape(author)}]]"',
        f"clipping_type: {clip_type}",
    ]
    # published / created 用裸值：types.json 已声明为 date，
    # 加引号会被 Obsidian 属性面板改回裸值（协作约定 §3.5）
    if clip.meta.published:
        lines.append(f"published: {clip.meta.published}")
    lines.append(f"created: {date.today().isoformat()}")
    lines.append(f'description: "{_yaml_escape(desc)}"')
    lines.append("tags:")
    lines += [f"  - {t}" for t in tags]
    lines.append("---")
    return "\n".join(lines)


def _info_callout(clip: schema.Clip) -> str:
    label = "视频信息" if clip.content_type == "video" else "笔记信息"
    rows = [f"> [!info] {label}"]
    if clip.meta.author:
        rows.append(f"> **作者**：{clip.meta.author}")
    if clip.content_type == "video":
        segs = len(clip.content.transcript)
        dur = hhmmss(clip.meta.duration_sec)
        rows.append(f"> **时长**：{dur}（{segs} 段）" if segs else f"> **时长**：{dur}")
    else:
        imgs = [a for a in clip.content_assets if a.kind == "image"]
        if imgs:
            rows.append(f"> **图片**：{len(imgs)} 张")
    if clip.meta.published:
        rows.append(f"> **发布**：{clip.meta.published}")
    s = clip.meta.stats
    if any((s.like, s.collect, s.comment, s.share)):
        bits = []
        if s.like:
            bits.append(f"{s.like} 赞")
        if s.collect:
            bits.append(f"{s.collect} 收藏")
        if s.comment:
            bits.append(f"{s.comment} 评论")
        rows.append(f"> **互动**：{' / '.join(bits)}")
    rows.append(f"> **链接**：{clip.canonical_url or clip.source_url}")
    return "\n".join(rows)


def _warning_callout(clip: schema.Clip) -> str:
    if not clip.provenance.warnings:
        return ""
    lines = ["> [!warning] " + clip.provenance.warnings[0]]
    for w in clip.provenance.warnings[1:]:
        lines.append(f"> {w}")
    return "\n".join(lines)


def _body_video(clip: schema.Clip) -> list[str]:
    """视频类正文：合并碎句成段，按时间戳标注。"""
    from core.textnorm import to_paragraphs

    segments = [{"start": s.start, "text": s.text} for s in clip.content.transcript]
    paras = to_paragraphs(segments)
    if not paras:
        return ["", "## 转写全文", "", "_（无内容）_"]

    lines = ["", "## 转写全文", ""]
    for p in paras:
        stamp = mmss(p["start"]) if p["start"] < 3600 else hhmmss(p["start"])
        lines.append(f"**[{stamp}]** {p['text']}")
        lines.append("")
    return lines


def _body_rich(clip: schema.Clip, assets: list[tuple[schema.Asset, str]]) -> list[str]:
    """图文类正文：正文文本 + 图内内容转写 + 原图嵌入。"""
    lines: list[str] = []

    if clip.content.text_blocks:
        lines += ["", "## 原文", ""]
        for b in clip.content.text_blocks:
            lines.append(b.text if b.type == "paragraph" else f"{b.type}: {b.text}")
            lines.append("")

    ocr_map = {o.order: o for o in clip.content.images_ocr}
    if ocr_map:
        lines += ["## 图内内容", ""]
        for asset, fname in assets:
            ocr = ocr_map.get(asset.order)
            caption = ""
            if ocr and ocr.text.strip():
                first = ocr.text.strip().splitlines()[0].lstrip("# ").strip()
                caption = first[:40]
            lines.append(f"![[{fname}]]")
            lines.append(f"*图 {asset.order}{'：' + caption if caption else ''}*")
            lines.append("")
            if ocr and ocr.text.strip():
                lines.append(ocr.text.strip())
                lines.append("")

    return lines


# ------------------------------------------------------------------ 附件
def _place_assets(
    clip: schema.Clip,
    note_name: str,
    cfg: config_mod.Config,
    *,
    dry_run: bool,
    result: PublishResult,
) -> list[tuple[schema.Asset, str]]:
    """把正文图片复制到 8_附件/{笔记名}/，返回 [(asset, 附件文件名)]。

    命名遵循 CAL 插件规则：{笔记名}-{YYYYMMDDHHmm}-{序号}.{ext}
    """
    images = [a for a in clip.content_assets if a.kind == "image"]
    if not images:
        return []

    images.sort(key=lambda a: a.order)
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    target_dir = cfg.vault.attachments / note_name
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    out: list[tuple[schema.Asset, str]] = []
    for i, asset in enumerate(images, 1):
        src = paths.PROJECT_ROOT / asset.path
        if not src.exists():
            result.warnings.append(f"附件源文件缺失，已跳过：{asset.path}")
            continue
        fname = f"{note_name}-{stamp}-{i}{src.suffix.lower() or '.jpg'}"
        if not dry_run:
            shutil.copy2(src, target_dir / fname)
            result.assets.append(target_dir / fname)
        out.append((asset, fname))
    return out


# ------------------------------------------------------------------ 校验
def _verify(cfg: config_mod.Config, note_name: str) -> list[str]:
    """跑 obsidian CLI 查断链。CLI 不可用时只提示，不阻断。"""
    vault = cfg.vault.root
    if not vault.exists():
        return [f"知识库目录不存在，跳过校验：{vault}"]
    try:
        proc = subprocess.run(
            ["obsidian", "unresolved"],
            cwd=str(vault), capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ["未找到 obsidian CLI 或调用超时，跳过断链校验"]

    if proc.returncode != 0:
        return ["obsidian CLI 返回非 0，跳过断链校验"]

    hits = [ln for ln in (proc.stdout or "").splitlines() if note_name in ln]
    if hits:
        return ["检测到断链（附件嵌入未解析）：\n  " + "\n  ".join(hits)]
    return []


# ------------------------------------------------------------------ 工具
# 平台文案里的噪声：抖音会在末尾附「……版本过低，升级后可展示全部信息」
_PLATFORM_JUNK_RE = re.compile(
    r"(?:…{2,}|\.{3,})[^。！？\n]{0,40}(?:版本过低|升级|下载|客户端|复制打开)[^\n]*$"
)
_HASHTAG_RE = re.compile(r"#[^\s#]+")

# auto 模式下平台文案的最小可用长度；短于此视为只有话题标签，退回转写
_AUTO_MIN_PLATFORM_LEN = 20


def _clean_platform_text(s: str) -> str:
    """清洗平台自带文案：去末尾的客户端提示、去话题标签、折行压平。"""
    s = _PLATFORM_JUNK_RE.sub("", s or "")
    s = _HASHTAG_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _description_source(clip: schema.Clip, mode: str = "auto") -> str:
    """决定 frontmatter `description` 取什么文本。

    语义对齐 importer：importer 写进去的是**来源自身的文案**（如知乎回答的开头、
    网页的 meta description），不是机器加工过的文本。

    因此默认优先取平台自带文案，而不是 ASR 转写 —— 转写含同音字错误
    （本次实例里「RAW 原片」被识别成「REW圆片」、「手选」被识别成「首选」），
    落进属性面板就是脏数据；平台文案是人写的，且天然就是「来源简介」。
    平台文案为空或只剩话题标签时，才退回转写。

    mode 取值见 core/config.py::PublishConfig.description_source。
    """
    from core.textnorm import normalize

    def _flat(s: str) -> str:
        # 折行必须压平，否则换行符会落进 YAML 双引号串里把 frontmatter 撑破；
        # 同时做标点归一化（clip.json 存的是原始转写，半角标点很难看）
        return re.sub(r"\s+", " ", normalize(s or "")).strip()

    platform_text = _flat(_clean_platform_text(clip.meta.description))
    transcript_text = _flat(clip.full_text())

    if mode == "platform":
        picked = platform_text or transcript_text
    elif mode == "transcript":
        picked = transcript_text or platform_text
    else:  # auto
        picked = (
            platform_text
            if len(platform_text) >= _AUTO_MIN_PLATFORM_LEN
            else (transcript_text or platform_text)
        )
    return picked


def _clean_title(title: str) -> str:
    """去掉抖音标题被 yt-dlp 截断留下的 `...` 尾巴。"""
    title = (title or "").strip()
    title = re.sub(r"[\s。，,]*[^.。，,]{0,20}(\.{3}|…{1,2})$", "", title).strip()
    return title or "untitled"


def _sanitize(name: str, limit: int = 60) -> str:
    name = re.sub(_ILLEGAL, "", name or "").strip().strip(".")
    return (name[:limit] or "untitled").strip()


def _yaml_escape(s: str) -> str:
    """转义双引号包裹的 YAML 字符串。折行必须先压平，否则 frontmatter 会被撑破。"""
    s = re.sub(r"\s*\n\s*", " ", s or "")
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _backup(note_path: Path) -> Path:
    """替换同名笔记前先备份，不做直接删除。"""
    backup_dir = paths.PROJECT_ROOT / ".workbuddy" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = backup_dir / f"{note_path.stem}.{stamp}.md"
    shutil.move(str(note_path), str(dest))
    return dest
