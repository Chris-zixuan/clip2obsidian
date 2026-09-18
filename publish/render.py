"""入库层（L4）：clip.json → 符合知识库规范的 Obsidian 笔记。

职责边界
--------
- **代码负责**：文件名清洗、frontmatter 组装、正文结构、断链校验。
  这些是机械劳动，代码做才能保证每篇完全一致。
- **agent 负责**：标题精简、知识库标签选择（需语义判断）。
  通过参数传入，不在本层做判断。

落库口径
--------
- 落点：`0_Inbox/Clippings/`
- `类型: clippings`；平台差异用 `clipping_type` 表达，不建平台子目录
- 来源层字段（`title` / `source` / `author` / `published` / `created` /
  `clipping_type` / `description`）由本工具写入 —— 视同 importer 产物
- `tags` 只用受控词表标签，**不带** `clippings`
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core import config as config_mod
from core import digest as digest_mod
from core import paths, schema
from core.textnorm import hhmmss, mmss, truncate

# 文件名非法字符
_ILLEGAL = r'[\\/:*?"<>|#\[\]]'
# 平台 → clipping_type 值（与库内既有 zhihu-answer 命名风格一致）
CLIPPING_TYPES = {"bilibili": "bilibili-video"}


class PublishError(Exception):
    """入库失败。消息面向用户。"""


@dataclass
class PublishResult:
    path: Path
    note_name: str
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
        tags:   知识库受控标签（agent 决定，至少 1 个）
        digest: 摘要正文（digest.md 内容，agent 产出）
        title:  标题覆写（agent 精简后的标题），留空则用 clip.meta.title
    """
    cfg = cfg or config_mod.get()

    problems = clip.validate()
    if problems:
        raise PublishError(
            "clip.json 结构校验未通过，拒绝入库：\n  - " + "\n  - ".join(problems)
        )
    if not tags:
        raise PublishError(
            "没有指定知识库标签。每篇至少 1 个受控词表标签。\n"
            "  用法：--tags 生活,成长"
        )

    # 摘要（L3 产物）校验 —— 它是流水线里唯一由 agent 手写的一环，
    # 不把关的话「见原文」这类占位内容也会顺畅落库。
    report = digest_mod.check(digest, transcript=clip.full_text()) if digest.strip() else None
    if report and not report.ok:
        raise PublishError(
            "摘要未通过校验，拒绝入库：\n  - " + "\n  - ".join(report.problems)
        )

    note_title = _clean_title(title or clip.meta.title or clip.platform_id)
    note_name = _sanitize(note_title, cfg.publish.filename_max_len)
    outdir = cfg.vault.inbox
    note_path = outdir / f"{note_name}.md"

    result = PublishResult(path=note_path, note_name=note_name)
    if report:
        result.warnings.extend(report.warnings)
    if dry_run:
        return result

    outdir.mkdir(parents=True, exist_ok=True)
    if note_path.exists():
        result.replaced_backup = _backup(note_path)
        result.warnings.append(f"同名笔记已存在，旧版已备份到 {result.replaced_backup}")

    note_path.write_text(
        _compose(clip, note_title, tags, digest, cfg), encoding="utf-8"
    )

    if cfg.publish.verify_after_publish:
        result.warnings.extend(_verify(cfg, note_name))
    return result


# ------------------------------------------------------------------ 组装
def _compose(
    clip: schema.Clip,
    title: str,
    tags: list[str],
    digest: str,
    cfg: config_mod.Config,
) -> str:
    parts = [_frontmatter(clip, title, tags, cfg), "", _info_callout(clip)]

    if digest.strip():
        parts += ["", digest.strip()]

    warn = _warning_callout(clip)
    if warn:
        parts += ["", warn]

    parts += _body_video(clip)
    return "\n".join(parts).rstrip() + "\n"


def _frontmatter(clip: schema.Clip, title: str, tags: list[str], cfg: config_mod.Config) -> str:
    """来源层字段与既有剪藏保持一致。

    source_url / author 允许为空（本地导入常拿不到）。空值**不写对应行**，
    绝不写 `[[unknown]]` 这类脏值——缺了就是缺了，由用户在 publish 时补。
    """
    author = (clip.meta.author or "").strip()
    source = clip.canonical_url or clip.source_url
    clip_type = CLIPPING_TYPES.get(clip.platform, f"{clip.platform}-clip")
    desc = truncate(_description_source(clip, cfg.publish.description_source), 120)

    lines = [
        "---",
        "类型: clippings",
        f'title: "{_yaml_escape(title)}"',
    ]
    if source:
        lines.append(f'source: "{_yaml_escape(source)}"')
    if author:
        lines.append("author:")
        lines.append(f'  - "[[{_yaml_escape(author)}]]"')
    lines.append(f"clipping_type: {clip_type}")
    # published / created 用裸值：types.json 已声明为 date，加引号会被
    # Obsidian 属性面板改回裸值
    if clip.meta.published:
        lines.append(f"published: {clip.meta.published}")
    lines.append(f"created: {date.today().isoformat()}")
    lines.append(f'description: "{_yaml_escape(desc)}"')
    lines.append("tags:")
    lines += [f"  - {t}" for t in tags]
    lines.append("---")
    return "\n".join(lines)


def _info_callout(clip: schema.Clip) -> str:
    rows = ["> [!info] 视频信息"]
    if clip.meta.author:
        rows.append(f"> **作者**：{clip.meta.author}")
    segs = len(clip.content.transcript)
    dur = hhmmss(clip.meta.duration_sec)
    rows.append(f"> **时长**：{dur}（{segs} 段）" if segs else f"> **时长**：{dur}")
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
    link = clip.canonical_url or clip.source_url
    if link:
        rows.append(f"> **链接**：{link}")
    else:
        # 本地导入没有链接：用媒体文件名提示来源，不写脏值
        name = _local_origin_name(clip)
        rows.append(f"> **来源**：本地文件 {name}" if name else "> **来源**：本地文件")
    return "\n".join(rows)


def _local_origin_name(clip: schema.Clip) -> str:
    """从首个**绝对路径**媒体文件取文件名，用于「本地文件 xxx」提示。"""
    for a in clip.assets:
        p = Path(a.path)
        if p.is_absolute():
            return p.name
    return ""


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
        return ["检测到断链：\n  " + "\n  ".join(hits)]
    return []


# ------------------------------------------------------------------ 工具
# 平台文案里的噪声尾部
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

    语义对齐 importer：importer 写进去的是**来源自身的文案**，不是机器加工过的
    文本。因此默认优先取平台自带文案，而不是 ASR 转写 —— 转写含同音字错误
    （实例：「RAW 原片」被识别成「REW圆片」），落进属性面板就是脏数据。
    平台文案为空或只剩话题标签时，才退回转写。

    mode 取值见 core/config.py::PublishConfig.description_source。
    """
    from core.textnorm import normalize

    def _flat(s: str) -> str:
        # 折行必须压平，否则换行符会落进 YAML 双引号串里把 frontmatter 撑破
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
    """去掉标题被截断留下的 `...` 尾巴。"""
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
    backup_dir = paths.BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = backup_dir / f"{note_path.stem}.{stamp}.md"
    shutil.move(str(note_path), str(dest))
    return dest
