"""文本归一化：繁转简、标点统一、碎句合段。

ASR 输出必然需要这几步，独立成模块供所有平台复用。
"""

from __future__ import annotations

import re

try:  # zhconv 缺失时降级为不做繁转简，而不是崩掉
    from zhconv import convert as _zh_convert

    HAS_ZHCONV = True
except ImportError:  # pragma: no cover
    HAS_ZHCONV = False

# Whisper 中文常输出半角标点 —— 仅在中文语境下转全角
_PUNCT_MAP = {
    ",": "，",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
    "(": "（",
    ")": "）",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
# 中文之间的空格（ASR 常逐词加空格）
_CJK_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])")

_SENT_END = "。？！…”"


def is_cjk(ch: str) -> bool:
    return bool(ch) and bool(_CJK_RE.match(ch))


def to_simplified(text: str) -> str:
    """繁体转简体。

    Whisper 转长中文音频时，后半程会退化输出繁体，必须过一遍。
    签名承诺返回 str —— 传 None 时返回空串，不要把 None 漏给下游。
    """
    if not text or not HAS_ZHCONV:
        return text or ""
    return _zh_convert(text, "zh-cn")


def normalize_punct(text: str) -> str:
    """半角标点转全角，但不动英文/数字语境（如 3.5、a:b）。"""
    if not text:
        return text

    def _repl(m: re.Match) -> str:
        ch = m.group(0)
        before = m.string[m.start() - 1] if m.start() > 0 else ""
        after = m.string[m.end()] if m.end() < len(m.string) else ""
        if is_cjk(before) or is_cjk(after):
            return _PUNCT_MAP[ch]
        return ch

    return re.sub(r"[,?!:;()]", _repl, text)


def normalize(text: str, *, strip_cjk_space: bool = True) -> str:
    """完整归一化：繁转简 → 去中文间空格 → 标点转全角。"""
    text = to_simplified(text or "")
    if strip_cjk_space:
        text = _CJK_SPACE_RE.sub("", text)
    text = text.replace("\u3000", "")
    return normalize_punct(text)


def to_paragraphs(
    segments: list[dict] | list,
    *,
    max_len: int = 110,
    min_ratio: float = 0.6,
) -> list[dict]:
    """把碎句合并成自然段：遇到句末标点且够长就断段。

    Args:
        segments: [{"start": float, "text": str}, ...]
    Returns:
        [{"start": float, "text": str}, ...]
    """
    paras: list[dict] = []
    buf: list[str] = []
    start: float | None = None

    for s in segments:
        if isinstance(s, dict):
            text, seg_start = s.get("text", ""), s.get("start", 0.0)
        else:
            text, seg_start = getattr(s, "text", ""), getattr(s, "start", 0.0)

        text = normalize(text)
        if not text:
            continue
        if start is None:
            start = float(seg_start)

        buf.append(text)
        joined = "".join(buf)
        if (text[-1] in _SENT_END and len(joined) >= max_len * min_ratio) or len(joined) >= max_len:
            paras.append({"start": start, "text": joined})
            buf, start = [], None

    if buf:
        paras.append({"start": start or 0.0, "text": "".join(buf)})
    return paras


def mmss(sec: float) -> str:
    """秒 → mm:ss。"""
    m, s = divmod(int(sec or 0), 60)
    return f"{m:02d}:{s:02d}"


def hhmmss(sec: float) -> str:
    """秒 → hh:mm:ss（仅当超过 1 小时时用）。"""
    sec = int(sec or 0)
    if sec < 3600:
        return mmss(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def truncate(text: str, limit: int, *, ellipsis: str = "…") -> str:
    """按字符数截断并加省略号。"""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + ellipsis
