"""文本归一化测试。

ASR 的输出必然要过这几道处理：繁转简（Whisper 长音频后半程会退化成繁体）、
标点转全角（Whisper 中文常输出半角）、碎句合段。这些错了在笔记里一眼可见。
"""

import unittest

from core import textnorm as T


class TestToSimplified(unittest.TestCase):

    @unittest.skipUnless(T.HAS_ZHCONV, "未安装 zhconv，跳过繁转简断言")
    def test_converts_traditional(self):
        self.assertEqual(T.to_simplified("繁體字與筆記"), "繁体字与笔记")

    @unittest.skipUnless(T.HAS_ZHCONV, "未安装 zhconv，跳过繁转简断言")
    def test_leaves_simplified_untouched(self):
        self.assertEqual(T.to_simplified("已经是简体了"), "已经是简体了")

    def test_empty_safe(self):
        self.assertEqual(T.to_simplified(""), "")
        self.assertEqual(T.to_simplified(None), "")


class TestNormalizePunct(unittest.TestCase):

    def test_converts_between_cjk(self):
        self.assertEqual(T.normalize_punct("你好,世界"), "你好，世界")

    def test_converts_before_cjk(self):
        self.assertEqual(T.normalize_punct("注意:这是重点"), "注意：这是重点")

    def test_leaves_decimal_point(self):
        """. 不在转换表里，3.5 不能被改成 3。5。"""
        self.assertEqual(T.normalize_punct("精度 3.5 毫米"), "精度 3.5 毫米")

    def test_leaves_ascii_colon_between_latin(self):
        self.assertEqual(T.normalize_punct("ratio a:b"), "ratio a:b")

    def test_leaves_ascii_punctuation_in_code(self):
        self.assertEqual(T.normalize_punct("f(a, b)"), "f(a, b)")

    def test_converts_question_and_exclamation(self):
        self.assertEqual(T.normalize_punct("真的吗?太好了!"), "真的吗？太好了！")

    def test_converts_parentheses_next_to_cjk(self):
        self.assertEqual(T.normalize_punct("示例(说明)"), "示例（说明）")

    def test_empty_safe(self):
        self.assertEqual(T.normalize_punct(""), "")


class TestNormalize(unittest.TestCase):

    @unittest.skipUnless(T.HAS_ZHCONV, "未安装 zhconv，跳过繁转简断言")
    def test_traditional_becomes_simplified(self):
        self.assertEqual(T.normalize("這個工具很好用"), "这个工具很好用")

    def test_removes_space_between_cjk(self):
        self.assertEqual(T.normalize("这 个 工 具"), "这个工具")

    def test_keeps_space_around_latin(self):
        """中英混排的空格必须保留，否则「用 AI 选片」挤成一团。"""
        self.assertEqual(T.normalize("用 AI 选片"), "用 AI 选片")

    def test_removes_ideographic_space(self):
        self.assertEqual(T.normalize("前面\u3000后面"), "前面后面")

    def test_combines_all_steps(self):
        got = T.normalize("這 個 工具,很好用!")
        self.assertEqual(got, "这个工具，很好用！")

    def test_strip_cjk_space_can_be_disabled(self):
        self.assertEqual(T.normalize("这 个", strip_cjk_space=False), "这 个")

    def test_empty_safe(self):
        self.assertEqual(T.normalize(""), "")


class TestToParagraphs(unittest.TestCase):

    def test_merges_short_segments(self):
        segs = [
            {"start": 0.0, "text": "出门拍了2000张照片，"},
            {"start": 2.0, "text": "连拍自动归进相似组。"},
        ]
        paras = T.to_paragraphs(segs)
        self.assertEqual(len(paras), 1)
        self.assertEqual(paras[0]["start"], 0.0)
        self.assertIn("连拍自动归进相似组", paras[0]["text"])

    def test_breaks_when_accumulated_length_reaches_max(self):
        """断段单位是 segment 边界：单段过长不会切，累积到 max_len 才断。"""
        segs = [{"start": float(i), "text": "十个字符的内容啊"} for i in range(10)]
        paras = T.to_paragraphs(segs, max_len=20)
        self.assertGreater(len(paras), 1)
        for p in paras[:-1]:
            self.assertLessEqual(len(p["text"]), 24)

    def test_single_long_segment_is_not_split(self):
        segs = [{"start": 0.0, "text": "内容很长但不带句末标点" * 12}]
        paras = T.to_paragraphs(segs, max_len=40)
        self.assertEqual(len(paras), 1)

    def test_start_seconds_preserved(self):
        segs = [
            {"start": 30.0, "text": "第一段内容足够长了，可以断开。"},
            {"start": 90.0, "text": "第二段开始了。"},
        ]
        paras = T.to_paragraphs(segs, max_len=10)
        self.assertEqual(paras[0]["start"], 30.0)

    def test_skips_empty_text(self):
        segs = [{"start": 0.0, "text": ""}, {"start": 1.0, "text": "有内容。"}]
        paras = T.to_paragraphs(segs)
        self.assertEqual(len(paras), 1)

    def test_accepts_objects_not_only_dicts(self):
        class Seg:
            def __init__(self, start, text):
                self.start, self.text = start, text

        paras = T.to_paragraphs([Seg(0.0, "对象形式的输入也要支持。")])
        self.assertEqual(len(paras), 1)

    def test_empty_input(self):
        self.assertEqual(T.to_paragraphs([]), [])


class TestTimeFormatting(unittest.TestCase):

    def test_mmss(self):
        self.assertEqual(T.mmss(0), "00:00")
        self.assertEqual(T.mmss(9), "00:09")
        self.assertEqual(T.mmss(61), "01:01")
        self.assertEqual(T.mmss(600), "10:00")

    def test_mmss_handles_none(self):
        self.assertEqual(T.mmss(None), "00:00")

    def test_hhmmss_uses_mmss_under_one_hour(self):
        self.assertEqual(T.hhmmss(61), "01:01")

    def test_hhmmss_with_hours(self):
        self.assertEqual(T.hhmmss(3661), "01:01:01")


class TestTruncate(unittest.TestCase):

    def test_short_text_untouched(self):
        self.assertEqual(T.truncate("短", 10), "短")

    def test_long_text_gets_ellipsis(self):
        self.assertEqual(T.truncate("一二三四五六", 3), "一二三…")

    def test_exact_length_no_ellipsis(self):
        self.assertEqual(T.truncate("一二三", 3), "一二三")

    def test_whitespace_stripped_first(self):
        self.assertEqual(T.truncate("  内容  ", 10), "内容")

    def test_empty_safe(self):
        self.assertEqual(T.truncate("", 5), "")
        self.assertEqual(T.truncate(None, 5), "")


if __name__ == "__main__":
    unittest.main()
