"""平台路由测试。

输入往往是**整段分享文案**而不是干净 URL（抖音/小红书都是这个形态），
抠错链接或路由错平台，后面三层全白跑 —— 所以这里用真实分享文案做样本。
"""

import unittest

from core import registry
from core.registry import UnsupportedLink


# 真实样本，直接取自实际使用时的粘贴内容
DOUYIN_SHARE = (
    "3.87 复制打开抖音，看看【不知所的作品】摄影师不用熬夜选片了，"
    "5分钟选完 RAW 原片直接... https://v.douyin.com/HFhaOoj5OcI/ d@A.Ty vfo:/ :6pm 07/10"
)
XHS_SHARE = (
    "情侣长途自驾必备清单｜3次长途整理出来的 和对象跑过... "
    "https://xhslink.cn/o/UKWULDJKsT 保留口令，直达【小红书】围观~"
)


class TestExtractUrl(unittest.TestCase):

    def test_extracts_from_douyin_share_text(self):
        self.assertEqual(registry.extract_url(DOUYIN_SHARE), "https://v.douyin.com/HFhaOoj5OcI/")

    def test_extracts_from_xhs_share_text(self):
        self.assertEqual(registry.extract_url(XHS_SHARE), "https://xhslink.cn/o/UKWULDJKsT")

    def test_plain_url_passes_through(self):
        url = "https://www.douyin.com/video/7686034803698754161"
        self.assertEqual(registry.extract_url(url), url)

    def test_strips_trailing_ascii_punctuation(self):
        got = registry.extract_url("看这个 https://example.com/a.b.")
        self.assertEqual(got, "https://example.com/a.b")

    def test_does_not_swallow_trailing_chinese_punctuation(self):
        got = registry.extract_url("参考 https://example.com/x，然后")
        self.assertEqual(got, "https://example.com/x")

    def test_raises_when_no_url(self):
        with self.assertRaises(UnsupportedLink):
            registry.extract_url("这段话里没有任何链接")

    def test_raises_on_empty_input(self):
        with self.assertRaises(UnsupportedLink):
            registry.extract_url("")

    def test_dedupes_but_keeps_order(self):
        urls = registry.extract_urls("https://a.com/1 https://b.com/2 https://a.com/1")
        self.assertEqual(urls, ["https://a.com/1", "https://b.com/2"])

    def test_takes_first_url_when_several(self):
        got = registry.extract_url("https://first.com/x 和 https://second.com/y")
        self.assertEqual(got, "https://first.com/x")


class TestDetect(unittest.TestCase):

    def test_douyin_short_link(self):
        self.assertEqual(registry.detect("https://v.douyin.com/ABC/").name, "douyin")

    def test_douyin_long_link(self):
        self.assertEqual(
            registry.detect("https://www.douyin.com/video/7686034803698754161").name, "douyin"
        )

    def test_douyin_share_path(self):
        self.assertEqual(
            registry.detect("https://www.douyin.com/share/video/123456").name, "douyin"
        )

    def test_xhs_short_link(self):
        self.assertEqual(registry.detect("https://xhslink.cn/o/UKWULDJKsT").name, "xiaohongshu")

    def test_xhs_explore_link(self):
        self.assertEqual(
            registry.detect("https://www.xiaohongshu.com/explore/6aa7cba2").name, "xiaohongshu"
        )

    def test_xhs_discovery_link(self):
        self.assertEqual(
            registry.detect("https://www.xiaohongshu.com/discovery/item/6aa7cba2").name,
            "xiaohongshu",
        )

    def test_unregistered_platform_raises(self):
        with self.assertRaises(UnsupportedLink):
            registry.detect("https://www.zhihu.com/question/123")

    def test_error_message_lists_supported_platforms(self):
        with self.assertRaises(UnsupportedLink) as ctx:
            registry.detect("https://example.com/x")
        self.assertIn("抖音", str(ctx.exception))
        self.assertIn("小红书", str(ctx.exception))


class TestGet(unittest.TestCase):

    def test_get_known_platform(self):
        self.assertEqual(registry.get("douyin").label, "抖音")
        self.assertEqual(registry.get("xiaohongshu").label, "小红书")

    def test_get_unknown_platform_raises(self):
        with self.assertRaises(UnsupportedLink):
            registry.get("bilibili")

    def test_registered_platforms_have_matching_module(self):
        """注册表与 pipelines/ 目录名必须一致，否则 import 时才炸。"""
        for spec in registry.PLATFORMS:
            self.assertEqual(spec.module, spec.name)


if __name__ == "__main__":
    unittest.main()
