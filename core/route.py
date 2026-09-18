"""L1 路由：判定「这是什么类型的素材」以及「属于哪个平台」。

职责边界
--------
只做**判定**，不落盘、不做转换：
- 类型判定：视频 / 音频 / 图文（图片集合）/ 压缩包
- 目录二义性消解：一堆文件是「批量素材」还是「一条图文」
- 平台与元信息降级链：能从文件名 / 伴随文件里认出什么就认什么，认不出留空

**铁律：拿不到就留空，绝不猜测。** 宁可笔记缺少作者，也不要填一个编造的作者。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core import paths

# ------------------------------------------------------------------ 素材类型
VIDEO = "video"
AUDIO = "audio"
IMAGE_SET = "image_set"
# 压缩包是 L1 内部的中间态：解压后再重新判定，不会出现在 job.json 里
ARCHIVE = "archive"

VIDEO_EXT = (
    ".mp4", ".mov", ".mkv", ".webm", ".flv", ".m4v", ".avi", ".mpg", ".mpeg", ".ts",
)
AUDIO_EXT = (
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".amr",
)
IMAGE_EXT = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif", ".tif", ".tiff",
)
ARCHIVE_EXT = (".zip",)

# 扫描目录时跳过的目录名：版本库、虚拟环境、项目自身产物、macOS 压缩包元数据
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "raw", "work", "__MACOSX"}


class RouteError(Exception):
    """无法判定素材类型或输入不合法。消息面向用户。"""


# ------------------------------------------------------------------ 平台
@dataclass(frozen=True)
class PlatformSpec:
    """一个平台的识别信息。

    新增平台只需在这里加一条：类型判定与转换流程完全不认识平台，
    平台信息只影响「原生 id」与「能不能拼出规范链接」。
    """

    name: str
    label: str
    # 文件名特征：命中任一即判为该平台
    name_hints: tuple[str, ...] = ()
    # 从文件名 / 链接里抠原生 id 的正则
    id_pattern: str = ""
    # 能拼出规范链接就填模板；拼不出留空（例如需要额外 token 的平台）
    url_template: str = ""

    def extract_id(self, text: str) -> str:
        """从文本里抠原生 id；没有返回空串。

        与平台判定分开：判定命中「名字特征」就够，但拼链接需要的是具体 id ——
        混在一起会出现「认出是 B站却抠不出 BV 号」。
        """
        if self.id_pattern:
            m = re.search(self.id_pattern, text)
            if m:
                return m.group(0)
        return ""

    def matches(self, text: str) -> bool:
        low = text.lower()
        return any(h.lower() in low for h in self.name_hints)

    def url_for(self, native_id: str) -> str:
        return self.url_template.format(id=native_id) if (self.url_template and native_id) else ""


PLATFORMS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="bilibili",
        label="B站",
        name_hints=("哔哩哔哩", "bilibili"),
        id_pattern=r"BV1[0-9A-Za-z]{8,}",
        url_template="https://www.bilibili.com/video/{id}",
    ),
    PlatformSpec(
        name="douyin",
        label="抖音",
        name_hints=("抖音", "douyin"),
        id_pattern=r"(?<!\d)\d{17,20}(?!\d)",
        url_template="https://www.douyin.com/video/{id}",
    ),
    PlatformSpec(
        name="xiaohongshu",
        label="小红书",
        name_hints=("小红书", "xiaohongshu", "xhs"),
        # 笔记 id 是 24 位十六进制串
        id_pattern=r"(?<![0-9a-fA-F])[0-9a-f]{24}(?![0-9a-fA-F])",
        # 拼不出可访问链接：正式链接需要 xsec_token，缺了就是打不开的 URL
        url_template="",
    ),
)


def detect_platform(name: str) -> PlatformSpec | None:
    """从文件名 / 链接推断平台。识别不出返回 None（不报错：平台只是锦上添花）。"""
    for spec in PLATFORMS:
        if spec.matches(name) or spec.extract_id(name):
            return spec
    return None


# ------------------------------------------------------------------ 类型判定
def kind_of(path: str | Path) -> str | None:
    """按扩展名判定单个文件的素材类型；不认识返回 None。"""
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXT:
        return VIDEO
    if suffix in AUDIO_EXT:
        return AUDIO
    if suffix in IMAGE_EXT:
        return IMAGE_SET
    if suffix in ARCHIVE_EXT:
        return ARCHIVE
    return None


def is_archive(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXT


def natural_key(path: str | Path):
    """自然排序键：`2.jpg` 排在 `10.jpg` 前面。

    Windows/macOS 资源管理器是自然序，Python 的默认字典序会让页序错乱
    （10 排在 2 前面），图文笔记的图片顺序必须与用户看到的顺序一致。
    """
    name = Path(path).name
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


@dataclass(frozen=True)
class Task:
    """一条待处理素材。"""

    kind: str
    path: Path
    images: tuple[Path, ...] = field(default=())   # 仅 image_set 使用，已自然排序


def resolve(path: str | Path) -> list[Task]:
    """把一次输入展开成任务列表。

    单文件 → 一个任务；目录 → 见 `resolve_dir`；压缩包 → 交给调用方解压后重判。
    """
    p = paths.expand(path)
    if not p.exists():
        raise RouteError(f"找不到输入：{p}")

    if p.is_file():
        kind = kind_of(p)
        if kind is None:
            raise RouteError(
                f"不认识的素材类型：{p.name}\n"
                f"  支持视频 {list(VIDEO_EXT)}、音频 {list(AUDIO_EXT)}、"
                f"图片 {list(IMAGE_EXT)}、压缩包 {list(ARCHIVE_EXT)}"
            )
        return [Task(kind, p, (p,) if kind == IMAGE_SET else ())]

    return resolve_dir(p)


def resolve_dir(directory: str | Path) -> list[Task]:
    """目录消解：一堆文件到底是「批量素材」还是「一条图文」。

    判定规则（顺序即优先级）：
    1. 目录里有视频 / 音频 / 压缩包 → 按**批量**处理，每项一个任务
    2. 否则只有图片 → 按**一条图文**处理，图片按自然序排列
    3. 都没有 → 报错并说明判定规则（不静默跳过，否则用户不知道为何没反应）
    """
    d = paths.expand(directory)
    if not d.is_dir():
        raise RouteError(f"不是目录：{d}")

    files = list(_walk_files(d))
    media = [p for p in files if kind_of(p) in (VIDEO, AUDIO)]
    archives = [p for p in files if is_archive(p)]
    images = [p for p in files if kind_of(p) == IMAGE_SET]

    if media or archives:
        items = sorted([*media, *archives], key=natural_key)
        return [Task(kind_of(p) or ARCHIVE, p) for p in items]

    if images:
        return [Task(IMAGE_SET, d, tuple(sorted(images, key=natural_key)))]

    raise RouteError(
        f"目录里没有可处理的素材：{d}\n"
        f"  判定规则：含视频/音频/压缩包 → 批量逐项；只含图片 → 当作一条图文"
    )


def _walk_files(root: Path):
    """遍历目录下的文件，跳过隐藏项与项目产物目录。"""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in parts):
            continue
        yield p


# ------------------------------------------------------------------ 元信息降级链
# yt-dlp info.json 里我们关心的字段（只取这些，避免把 webpage_url 等噪声写进产物）
META_KEYS = (
    "title", "uploader", "channel", "owner", "author", "description",
    "upload_date", "duration", "tags", "like_count", "collect_count",
    "comment_count", "repost_count", "author_id", "uploader_id",
)


def title_from_filename(stem: str) -> str:
    """从文件名解析标题：剥掉下载器附加的平台后缀。

    例：`认识MAF_BV1xx411c7mD_哔哩哔哩_bilibili` → `认识MAF`。

    两轮清洗：先摘掉原生编号（BV 号 / 作品号已被单独提取，留在标题里是噪声），
    再循环剥平台后缀 —— 「平台中文名 + 英文名」常常连着出现，要剥多轮。
    """
    s = (stem or "").strip()

    for spec in PLATFORMS:
        if spec.id_pattern:
            s = re.sub(rf"[_\-\s]*{spec.id_pattern}[_\-\s]*", " ", s)

    hints: list[str] = []
    for spec in PLATFORMS:
        hints += [spec.name, spec.label, *spec.name_hints]

    changed = True
    while changed:
        changed = False
        for hint in hints:
            new = re.sub(rf"[_\-\s]+{re.escape(hint)}\s*$", "", s, flags=re.I)
            if new != s:
                s = new.strip()
                changed = True

    return re.sub(r"\s{2,}", " ", s).strip(" _-")


def native_id(spec: PlatformSpec | None, *texts: str, info_json: dict | None = None) -> str:
    """原生 id（BV 号 / 作品号 / 笔记 id），拿不到返回空串。

    来源优先级：文件名 → 命令行给的链接 → 伴随文件里的 info.json。
    **不参与条目 id**：用户删掉文件名里的编号，素材不该变成新条目。
    """
    if spec is None:
        return ""
    for text in texts:
        if text and (found := spec.extract_id(text)):
            return found
    value = (info_json or {}).get("id")
    if isinstance(value, str):
        return spec.extract_id(value)
    return ""


def read_sidecar(base: str, search_dir: Path) -> tuple[dict | None, str | None]:
    """读同目录同名伴随文件：`.json`（元信息）/ `.url`（链接）。

    兼容 yt-dlp 的 `{base}.info.json`——其 stem 是 `{base}.info`，匹配时先剥掉。
    """
    info_json: dict | None = None
    link: str | None = None
    search_dir = Path(search_dir)
    if not search_dir.is_dir():
        return None, None

    for f in search_dir.iterdir():
        if not f.is_file():
            continue
        cand = f.stem[: -len(".info")] if f.stem.endswith(".info") else f.stem
        if cand != base:
            continue
        suffix = f.suffix.lower()
        if suffix == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    info_json = data
            except (json.JSONDecodeError, OSError):
                pass
        elif suffix == ".url":
            link = link or first_url(f.read_text(encoding="utf-8", errors="ignore"))
    return info_json, link


def first_url(text: str) -> str | None:
    """从一段文本里抠出第一个链接，并剥掉尾随的标点（中英文都要剥 ——
    中文文案里链接后面常紧跟句号，带着句号的 URL 是打不开的）。"""
    m = re.search(r"https?://[^\s\"'<>]+", text or "")
    return m.group(0).rstrip(".,;:!?。，；：！？）】》") if m else None


def assemble_meta(info_json: dict | None, filename_title: str = "") -> dict:
    """整理成精简 meta。缺失字段一律留空，绝不从外部知识补。"""
    meta: dict = {
        "title": "",
        "author": "",
        "description": "",
        "published": "",
        "duration_sec": 0,
        "topics": [],
    }
    if info_json:
        meta["title"] = str(info_json.get("title") or "").strip()
        meta["author"] = str(
            info_json.get("uploader") or info_json.get("channel")
            or info_json.get("owner") or info_json.get("author") or ""
        ).strip()
        meta["description"] = str(info_json.get("description") or "").strip()
        meta["published"] = _normalize_date(info_json.get("upload_date"))
        meta["duration_sec"] = _as_int(info_json.get("duration"))
        meta["topics"] = _topics(info_json)

    if filename_title and not meta["title"]:
        meta["title"] = filename_title.strip()
    return meta


def merge_meta(base: dict, extra: dict) -> dict:
    """把链接探来的元信息并入本地已有产出：**只补缺失，不覆盖已有值**。

    本地 sidecar 通常是同一个视频自己的 info.json，比事后用链接探来的更贴近
    手里的文件；覆盖会让两边数据打架，而用户无从判断哪个对。
    """
    merged = dict(base or {})
    for key in META_KEYS:
        value = (extra or {}).get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (int, float)) and value == 0:
            continue
        if merged.get(key) not in (None, "", 0, []):
            continue   # 已有值：保留
        merged[key] = value
    return merged


def _normalize_date(value) -> str:
    """`20260901` → `2026-09-01`；认不出就留空。"""
    s = str(value or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return ""


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _topics(info_json: dict) -> list[str]:
    """平台话题（tags），非知识库标签。去重、限长。"""
    found: list[str] = []
    for item in info_json.get("tags") or []:
        name = item if isinstance(item, str) else (item or {}).get("name", "")
        name = str(name).strip()
        if name and name not in found:
            found.append(name)
    return found[:12]
