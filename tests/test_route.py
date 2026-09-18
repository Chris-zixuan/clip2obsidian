"""路由层测试（L1 的判定部分）。

这一层决定「这是什么」和「它属于哪个平台」，判错的代价很大：
类型判错会走错分支，平台判错会写错剪藏类型与链接。所以逐个扩展名、
逐个平台特征、逐层元信息降级都锁一遍。
"""

import tempfile
import unittest
from pathlib import Path

from core import route


class TestKindOf(unittest.TestCase):

    def test_video_extensions(self):
        for name in ("a.mp4", "a.MOV", "a.mkv", "a.webm", "a.avi"):
            self.assertEqual(route.kind_of(name), route.VIDEO, name)

    def test_audio_extensions(self):
        for name in ("a.mp3", "a.WAV", "a.m4a", "a.flac", "a.opus"):
            self.assertEqual(route.kind_of(name), route.AUDIO, name)

    def test_image_extensions(self):
        for name in ("a.jpg", "a.JPEG", "a.png", "a.heic", "a.webp"):
            self.assertEqual(route.kind_of(name), route.IMAGE_SET, name)

    def test_archive(self):
        self.assertEqual(route.kind_of("a.zip"), route.ARCHIVE)
        self.assertTrue(route.is_archive("A.ZIP"))

    def test_unknown(self):
        self.assertIsNone(route.kind_of("a.txt"))
        self.assertIsNone(route.kind_of("a.pdf"))


class TestPlatformDetect(unittest.TestCase):

    def test_bilibili_by_hint(self):
        self.assertEqual(route.detect_platform("某视频_哔哩哔哩_bilibili.mp4").name, "bilibili")

    def test_bilibili_by_bv(self):
        self.assertEqual(route.detect_platform("BV1xx411c7mD.mp4").name, "bilibili")

    def test_douyin_by_id(self):
        """抖音的文件名常常就是一串 17–20 位作品号。"""
        self.assertEqual(route.detect_platform("7683504084386100923.mp4").name, "douyin")

    def test_douyin_id_must_be_whole_number(self):
        """分辨率之类的短数字不该被当成作品号。"""
        self.assertIsNone(route.detect_platform("1920x1080.mp4"))

    def test_xiaohongshu_by_hint(self):
        self.assertEqual(route.detect_platform("小红书笔记_封面.jpg").name, "xiaohongshu")

    def test_unknown_returns_none(self):
        self.assertIsNone(route.detect_platform("未命名.mp4"))


class TestNativeId(unittest.TestCase):

    def setUp(self):
        self.bili = route.detect_platform("BV1xx411c7mD")
        self.none_spec = None

    def test_from_filename(self):
        self.assertEqual(
            route.native_id(self.bili, "视频_BV1xx411c7mD_哔哩哔哩_bilibili.mp4"),
            "BV1xx411c7mD",
        )

    def test_from_url_when_filename_has_none(self):
        self.assertEqual(
            route.native_id(self.bili, "未命名.mp4", "https://www.bilibili.com/video/BV1yy411c7mE"),
            "BV1yy411c7mE",
        )

    def test_from_info_json_last(self):
        self.assertEqual(
            route.native_id(self.bili, "未命名.mp4", info_json={"id": "BV1zz411c7mF"}),
            "BV1zz411c7mF",
        )

    def test_filename_wins(self):
        self.assertEqual(
            route.native_id(self.bili, "A_BV1aa411c7mA.mp4", info_json={"id": "BV1bb411c7mB"}),
            "BV1aa411c7mA",
        )

    def test_no_spec_returns_empty(self):
        self.assertEqual(route.native_id(self.none_spec, "BV1xx411c7mD"), "")


class TestTitleFromFilename(unittest.TestCase):
    """标题清洗要同时摘掉原生编号与平台后缀 —— 它们是噪声，不该进笔记标题。"""

    def test_strips_id_and_suffix(self):
        self.assertEqual(
            route.title_from_filename("认识MAF_BV1xx411c7mD_哔哩哔哩_bilibili"),
            "认识MAF",
        )

    def test_strips_plain_suffix(self):
        self.assertEqual(route.title_from_filename("某视频_哔哩哔哩"), "某视频")
        self.assertEqual(route.title_from_filename("某视频-bilibili"), "某视频")

    def test_strips_douyin_suffix(self):
        self.assertEqual(route.title_from_filename("某视频_抖音"), "某视频")

    def test_keeps_normal_name(self):
        self.assertEqual(route.title_from_filename("普通文件名"), "普通文件名")

    def test_empty(self):
        self.assertEqual(route.title_from_filename(""), "")


class TestNaturalKey(unittest.TestCase):
    """页序必须与资源管理器一致，否则图文笔记的图片顺序会错乱。"""

    def test_numbers_sorted_naturally(self):
        names = ["10.jpg", "2.jpg", "1.jpg"]
        ordered = sorted(names, key=route.natural_key)
        self.assertEqual(ordered, ["1.jpg", "2.jpg", "10.jpg"])

    def test_preserves_non_numeric_order(self):
        names = ["b.jpg", "a.jpg"]
        self.assertEqual(sorted(names, key=route.natural_key), ["a.jpg", "b.jpg"])


class TestResolve(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _touch(self, relative: str, content: bytes = b"x") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_single_video_file(self):
        path = self._touch("a.mp4")
        tasks = route.resolve(path)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, route.VIDEO)

    def test_single_image_becomes_one_image_set(self):
        path = self._touch("a.jpg")
        tasks = route.resolve(path)
        self.assertEqual(tasks[0].kind, route.IMAGE_SET)
        # 比较文件名而非 Path 本体：resolve() 会把 /var 展开成 /private/var
        self.assertEqual([p.name for p in tasks[0].images], ["a.jpg"])

    def test_archive_file_passes_through(self):
        path = self._touch("a.zip")
        self.assertEqual(route.resolve(path)[0].kind, route.ARCHIVE)

    def test_missing_input_raises(self):
        with self.assertRaises(route.RouteError):
            route.resolve(self.root / "不存在.mp4")

    def test_unknown_extension_raises(self):
        path = self._touch("说明.txt")
        with self.assertRaises(route.RouteError):
            route.resolve(path)

    def test_dir_with_media_is_batch(self):
        self._touch("a.mp4")
        self._touch("b.mp3")
        tasks = route.resolve_dir(self.root)
        self.assertEqual([t.kind for t in tasks], [route.VIDEO, route.AUDIO])

    def test_dir_with_media_and_images_is_batch(self):
        """有媒体时按批量处理，图片不参与（它们是视频封面之类的附属物）。"""
        self._touch("a.mp4")
        self._touch("cover.jpg")
        tasks = route.resolve_dir(self.root)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, route.VIDEO)

    def test_dir_with_only_images_is_one_image_set(self):
        self._touch("2.jpg")
        self._touch("10.jpg")
        tasks = route.resolve_dir(self.root)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].kind, route.IMAGE_SET)
        self.assertEqual([p.name for p in tasks[0].images], ["2.jpg", "10.jpg"])

    def test_dir_images_in_subdirs_are_collected(self):
        self._touch("a/1.jpg")
        self._touch("b/2.jpg")
        tasks = route.resolve_dir(self.root)
        self.assertEqual(len(tasks[0].images), 2)

    def test_dir_skips_hidden_and_project_dirs(self):
        self._touch(".DS_Store")
        self._touch("__MACOSX/._a.jpg")
        self._touch("real.jpg")
        tasks = route.resolve_dir(self.root)
        self.assertEqual([p.name for p in tasks[0].images], ["real.jpg"])

    def test_empty_dir_raises_with_rule_explained(self):
        with self.assertRaises(route.RouteError) as ctx:
            route.resolve_dir(self.root)
        self.assertIn("判定规则", str(ctx.exception))


class TestSidecar(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_reads_ytdlp_info_json(self):
        (self.dir / "video.info.json").write_text(
            '{"title": "标题", "uploader": "作者"}', encoding="utf-8"
        )
        info, link = route.read_sidecar("video", self.dir)
        self.assertEqual(info["title"], "标题")
        self.assertIsNone(link)

    def test_reads_url_file(self):
        (self.dir / "video.url").write_text(
            "https://www.bilibili.com/video/BV1xx411c7mD", encoding="utf-8"
        )
        _info, link = route.read_sidecar("video", self.dir)
        self.assertEqual(link, "https://www.bilibili.com/video/BV1xx411c7mD")

    def test_no_match(self):
        self.assertEqual(route.read_sidecar("nothing", self.dir), (None, None))

    def test_broken_json_ignored(self):
        (self.dir / "video.json").write_text("{不是 json", encoding="utf-8")
        self.assertEqual(route.read_sidecar("video", self.dir), (None, None))

    def test_first_url_strips_trailing_punctuation(self):
        self.assertEqual(
            route.first_url("看这个 https://a.com/x。"), "https://a.com/x"
        )


class TestMetaAssemble(unittest.TestCase):
    """元信息铁律：拿不到就留空，绝不猜测。"""

    def test_empty_info_keeps_blank_title_but_uses_filename(self):
        meta = route.assemble_meta(None, "文件名标题")
        self.assertEqual(meta["title"], "文件名标题")
        self.assertEqual(meta["author"], "")
        self.assertEqual(meta["published"], "")

    def test_upload_date_normalized(self):
        meta = route.assemble_meta({"upload_date": "20260901"})
        self.assertEqual(meta["published"], "2026-09-01")

    def test_bad_date_left_blank(self):
        self.assertEqual(route.assemble_meta({"upload_date": "unknown"})["published"], "")

    def test_author_field_fallbacks(self):
        for key in ("uploader", "channel", "owner", "author"):
            self.assertEqual(route.assemble_meta({key: "作者"})["author"], "作者", key)

    def test_topics_dedup_and_cap(self):
        meta = route.assemble_meta({"tags": ["a", "a"] + [f"t{i}" for i in range(20)]})
        self.assertEqual(meta["topics"][0], "a")
        self.assertEqual(len(meta["topics"]), 12)

    def test_merge_meta_only_fills_missing(self):
        base = {"title": "已有", "author": ""}
        merged = route.merge_meta(base, {"title": "新的", "author": "作者", "upload_date": "20260901"})
        self.assertEqual(merged["title"], "已有")
        self.assertEqual(merged["author"], "作者")
        self.assertEqual(merged["upload_date"], "20260901")

    def test_merge_meta_skips_empty_and_zero(self):
        merged = route.merge_meta({}, {"title": "  ", "duration": 0})
        self.assertNotIn("title", merged)
        self.assertNotIn("duration", merged)


class TestUrlForPlatform(unittest.TestCase):

    def test_bilibili_url_from_bv(self):
        spec = route.detect_platform("BV1xx411c7mD")
        self.assertEqual(spec.url_for("BV1xx411c7mD"), "https://www.bilibili.com/video/BV1xx411c7mD")

    def test_xiaohongshu_has_no_template(self):
        """笔记链接需要额外 token，拼出来也打不开 —— 宁可不拼。"""
        spec = route.detect_platform("小红书")
        self.assertEqual(spec.url_for("0123456789abcdef01234567"), "")


if __name__ == "__main__":
    unittest.main()
