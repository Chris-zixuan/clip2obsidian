"""digest.md 校验（L3 → L4 之间的契约）。

为什么需要
----------
L3 的摘要是整条流水线里**唯一没有机器把关**的一环：它由 agent 手写，写歪了、
写成原文复读机、写成「见原文」的索引占位符，都会一路顺畅地落进知识库。
L1/L2/L4 各自都有结构校验，唯独这里凭自觉。

所以把它变成可检查的：L4 入库前跑一遍 `check()`，有硬问题就拒绝落库。

边界
----
只查**结构与明显的质量问题**（空、过短、带 frontmatter、整段照抄原文），
不评判文笔与观点 —— 那是人的事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 正文（去掉 Markdown 标记后）的最小字数：低于此基本是索引占位符
MIN_CHARS = 50
# 超过此长度提示精简（不是错误：长本身不代表错）
MAX_CHARS = 3000
# 与转写逐字重复超过这个长度，视为照抄
DUP_MIN_LEN = 30

# 统计字数时剥掉的 Markdown 噪声字符
_MD_NOISE_RE = re.compile(r"[#*`>\[\]()!\-—\s]")


@dataclass
class DigestReport:
    """校验结果。`problems` 非空即拒绝入库。"""

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(text: str, *, transcript: str = "") -> DigestReport:
    """校验摘要。

    Args:
        text:       digest.md 全文
        transcript: 转写全文（可选）。给了才做照抄检测。
    """
    problems: list[str] = []
    warnings: list[str] = []

    stripped = (text or "").strip()
    if not stripped:
        return DigestReport(["摘要为空。L3 必须产出能脱离原文独立阅读的摘要。"])

    # frontmatter 由 L4 组装：摘要里带了会把整篇笔记的属性搞乱
    if stripped.startswith("---"):
        problems.append(
            "摘要开头就是 frontmatter（---）。frontmatter 由入库层组装，"
            "摘要正文不该包含它。"
        )

    body_chars = len(_MD_NOISE_RE.sub("", stripped))
    if body_chars < MIN_CHARS:
        problems.append(
            f"摘要正文只有 {body_chars} 字（要求 ≥ {MIN_CHARS}）。"
            f"索引占位符、'见原文' 这类内容没有入库价值。"
        )
    elif body_chars > MAX_CHARS:
        warnings.append(f"摘要 {body_chars} 字，超过 {MAX_CHARS}，建议精简。")

    if not re.search(r"^#{1,3}\s+\S", stripped, re.M):
        warnings.append("摘要没有任何小标题，建议至少分「要点 / 我的思考」两节。")

    if transcript and _looks_like_copy(stripped, transcript):
        warnings.append(
            "摘要里有整句与转写逐字相同，疑似原文复读。摘要应是可脱离原文阅读的"
            "提炼，而不是拷贝。"
        )

    return DigestReport(problems, warnings)


def _looks_like_copy(digest: str, transcript: str) -> bool:
    """是否存在从原文整句照抄的行。"""
    flat_transcript = re.sub(r"\s+", "", transcript or "")
    if len(flat_transcript) < DUP_MIN_LEN:
        return False
    for line in digest.splitlines():
        line = line.strip().lstrip("-*#>").strip()
        if len(line) < DUP_MIN_LEN:
            continue
        if re.sub(r"\s+", "", line) in flat_transcript:
            return True
    return False
