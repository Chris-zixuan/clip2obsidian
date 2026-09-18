"""摘要校验测试（L3 → L4 的契约）。

L3 的摘要由 agent 手写，是整条流水线里唯一没有机器把关的一环。这里的用例
锁住「什么样的摘要不许进知识库」：索引占位符、带 frontmatter、整段照抄原文。

注意只查结构与明显质量问题 —— 文笔不归机器管，所以不存在「观点是否正确」
这类断言。
"""

import unittest

from core import digest as D


# 一份结构完整、长度合规、不照抄原文的摘要
GOOD = (
    "## 要点\n\n"
    "- 连拍会自动归进相似组，AI 先粗筛，最后留哪张仍由摄影师决定。\n"
    "- 两千张照片五分钟筛完，省下的是体力活而不是审美判断。\n\n"
    "## 我的思考\n\n"
    "- 可以拿来对照自己的选片流程，看哪一步一直在重复劳动。"
)

TRANSCRIPT = "出门拍了2000张照片，用AI五分钟选完，连拍自动归进相似组，真的不用熬夜手选了。"


class TestHardProblems(unittest.TestCase):
    """problems 非空 → 拒绝入库。"""

    def test_empty_digest_rejected(self):
        self.assertFalse(D.check("").ok)

    def test_whitespace_only_rejected(self):
        self.assertFalse(D.check("   \n\n  ").ok)

    def test_index_placeholder_rejected(self):
        """「见原文」这类占位内容没有入库价值。"""
        report = D.check("见原文。")
        self.assertFalse(report.ok)
        self.assertTrue(any("字" in p for p in report.problems))

    def test_frontmatter_rejected(self):
        """frontmatter 由 L4 组装；摘要里带了会把整篇笔记的属性搞乱。"""
        report = D.check("---\ntitle: x\n---\n\n" + GOOD)
        self.assertFalse(report.ok)
        self.assertTrue(any("frontmatter" in p for p in report.problems))

    def test_good_digest_passes(self):
        report = D.check(GOOD)
        self.assertTrue(report.ok)
        self.assertEqual(report.problems, [])


class TestWarnings(unittest.TestCase):
    """warnings 只提示，不阻断。"""

    def test_no_heading_warned(self):
        report = D.check("这是一段没有任何小标题的摘要，但它的正文长度是足够长的，" * 2)
        self.assertTrue(report.ok)
        self.assertTrue(any("小标题" in w for w in report.warnings))

    def test_too_long_warned(self):
        report = D.check("## 要点\n\n" + "字数很多很多的摘要内容。" * 300)
        self.assertTrue(report.ok)
        self.assertTrue(any("精简" in w for w in report.warnings))

    def test_copying_transcript_warned(self):
        """整句照抄原文 = 复读机，不是摘要。"""
        # 照抄一整句，再补一段自己的话（否则会先因过短被拒，测不到复读检测）
        copied = "## 要点\n\n" + TRANSCRIPT + "\n\n" + "后面这段是自己的话，用来把长度补过校验线。"
        report = D.check(copied, transcript=TRANSCRIPT)
        self.assertTrue(report.ok)
        self.assertTrue(any("复读" in w for w in report.warnings))

    def test_paraphrase_not_warned(self):
        """用自己的话重述不该被判成照抄。"""
        report = D.check(GOOD, transcript=TRANSCRIPT)
        self.assertFalse(any("复读" in w for w in report.warnings))

    def test_short_lines_are_not_checked_for_copy(self):
        """短句（如引用术语）不该触发复读检测。"""
        report = D.check("## 要点\n\n- 术语很短。\n\n" + "补充足够长度的正文内容以通过字数校验。" * 2,
                         transcript="术语很短。" * 10)
        self.assertFalse(any("复读" in w for w in report.warnings))


class TestCharCounting(unittest.TestCase):
    """字数统计要剥掉 Markdown 标记，否则标记本身也能凑数。"""

    def test_markdown_marks_not_counted(self):
        """全是标记、没有实词 → 仍应判过短。"""
        report = D.check("#" * 200)
        self.assertFalse(report.ok)

    def test_plain_text_is_counted(self):
        report = D.check("这段纯文本没有标记，长度足够通过校验，可以用来验证字数统计。" * 2)
        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
