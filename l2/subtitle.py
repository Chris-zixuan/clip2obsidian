"""本地字幕解析：srt / vtt / ass。

命中本地字幕就完全跳过转写 —— 既快（秒级 vs 分钟级），又准（没有同音字错误）。
所以它是视频分支的第一道短路，优先级高于任何转写服务。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.textnorm import normalize

LOCAL_SUB_EXT = (".srt", ".vtt", ".ass")

# 同扩展名内的质量排序：srt 最干净，vtt 次之，ass 常带样式标签
_QUALITY = {".srt": 0, ".vtt": 1, ".ass": 2}

# SRT / VTT 通用时间轴（HH:MM:SS,mmm 或 HH:MM:SS.mmm）
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_SUB_RE = re.compile(rf"{_TS}\s*-->\s*{_TS}[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)

# VTT 内联标签 <c>、<00:00:01.000> 等；ASS 样式标签 {\...}
_VTT_TAG_RE = re.compile(r"<[^>]+>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def find_local_subtitle(media_path: str | Path) -> Path | None:
    """在媒体文件同目录里找本地字幕。

    优先级：① 与媒体**同名前缀**的字幕；② 目录里其它字幕，按 srt > vtt > ass。
    """
    parent = Path(media_path).parent
    if not parent.is_dir():
        return None

    candidates = [f for f in parent.iterdir() if f.suffix.lower() in LOCAL_SUB_EXT]
    if not candidates:
        return None

    stem = Path(media_path).stem
    candidates.sort(key=lambda f: (
        0 if f.stem.startswith(stem) else 1,
        _QUALITY.get(f.suffix.lower(), 9),
        f.name,
    ))
    return candidates[0]


def parse_subtitle(path: str | Path) -> list[dict]:
    """把字幕解析为 `[{start, end, text}]`，按扩展名分派解析器。"""
    path = Path(path)
    if path.suffix.lower() == ".ass":
        return _parse_ass(path)
    return _parse_srt_vtt(path)


def _parse_srt_vtt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[dict] = []
    for match in _SUB_RE.finditer(raw):
        start = _to_sec(match.group(1), match.group(2), match.group(3), match.group(4))
        end = _to_sec(match.group(5), match.group(6), match.group(7), match.group(8))
        # 先折行、再归一化、最后去标签：normalize 会把半角标点转全角，
        # 若先去掉 <...> 标签，标签里的属性值可能干扰归一化
        text = normalize(re.sub(r"\s*\n\s*", " ", (match.group(9) or "").strip()))
        text = _VTT_TAG_RE.sub("", text).strip()
        if not text:
            continue
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return segments


def _parse_ass(path: Path) -> list[dict]:
    """解析 ASS：Dialogue 行按前 9 个逗号切分，第 10 段才是文本（文本里可能有逗号）。"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[dict] = []
    in_events = False

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.startswith("[Events]")
            continue
        if not in_events or not line.startswith("Dialogue:"):
            continue

        parts = line[len("Dialogue:"):].strip().split(",", 9)
        if len(parts) < 10:
            continue

        text = parts[9].strip().replace("\\N", " ").replace("\\n", " ")
        text = normalize(_ASS_TAG_RE.sub("", text))
        if not text:
            continue
        segments.append({
            "start": round(_ass_to_sec(parts[1].strip()), 2),
            "end": round(_ass_to_sec(parts[2].strip()), 2),
            "text": text,
        })
    return segments


def _to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _ass_to_sec(text: str) -> float:
    """ASS 时间码 `H:MM:SS.cc`（厘秒）。"""
    hours, minutes, rest = text.split(":")
    seconds, centis = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(centis) / 100
