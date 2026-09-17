"""clip.json 契约层测试。

这是四层之间唯一的通信契约，一旦序列化/校验出问题，所有平台同时失效 ——
所以这里覆盖的是**结构性回归**，不是业务质量。

其中 test_roundtrip_* 两条是真实 bug 的回归测试：`from_dict` 曾把
`meta` / `assets` / `content` / `provenance` 这些嵌套键既通过 `_pick`
展开、又显式传一次，导致 `TypeError: got multiple values for keyword
argument` —— 所有 clip.json 都读不回来。
"""

import json
import tempfile
import unittest
from pathlib import Path

from core import schema
from core.schema import Clip, Meta, Stats


def _video_clip(**overrides) -> Clip:
    """一个结构完整、能通过校验的 video clip。"""
    base = dict(
        id="douyin:7686034803698754161",
        platform="douyin",
        content_type="video",
        source_url="https://www.douyin.com/video/7686034803698754161",
        canonical_url="https://www.douyin.com/video/7686034803698754161",
        fetched_at="2026-09-17T11:20:00+08:00",
        meta=Meta(
            title="测试标题",
            author="不知所",
            published="2026-09-16",
            duration_sec=61,
            stats=Stats(like=100, collect=20, comment=3, share=1),
            topics=["摄影", "AI选片"],
            description="出门拍了2000张照片，用AI五分钟选完。",
        ),
        assets=[
            schema.Asset(kind="cover", path="raw/douyin_x/cover.jpg", order=0, role="cover"),
            schema.Asset(kind="video", path="raw/douyin_x/media.mp4", order=0, role="content"),
        ],
        content=schema.Content(
            transcript=[
                schema.Segment(start=0.0, end=3.2, text="出门拍了2000张照片，"),
                schema.Segment(start=3.2, end=6.4, text="用AI五分钟选完。"),
            ],
        ),
        provenance=schema.Provenance(
            fetcher="yt-dlp@2026.09.15",
            extractor="asr:faster:medium",
            warnings=["转写由语音识别生成，可能有同音字错误。"],
        ),
    )
    base.update(overrides)
    return Clip(**base)


def _image_text_clip(ocr_status: str = "done") -> Clip:
    return Clip(
        id="xiaohongshu:6aa7cba2000000002803b79e",
        platform="xiaohongshu",
        content_type="image_text",
        source_url="https://www.xiaohongshu.com/explore/6aa7cba2",
        meta=Meta(title="情侣长途自驾必备清单", author="爱折腾的老吴"),
        assets=[
            schema.Asset(kind="cover", path="raw/xhs/01.jpg", order=0, role="cover"),
            schema.Asset(kind="image", path="raw/xhs/02.jpg", order=1, role="content"),
        ],
        content=schema.Content(
            text_blocks=[schema.TextBlock(type="paragraph", text="整理出这份清单。")],
            images_ocr=[schema.ImageOcr(order=1, text="车辆应急：充气泵", status=ocr_status)],
        ),
    )


class TestSerializationByRoundTrip(unittest.TestCase):
    """to_dict → from_dict → to_dict 必须完全一致。"""

    def test_roundtrip_video_clip(self):
        clip = _video_clip()
        first = clip.to_dict()
        again = Clip.from_dict(first).to_dict()
        self.assertEqual(first, again)

    def test_roundtrip_image_text_clip(self):
        clip = _image_text_clip()
        first = clip.to_dict()
        again = Clip.from_dict(first).to_dict()
        self.assertEqual(first, again)

    def test_roundtrip_survives_json(self):
        """经真实 JSON 文本走一遍（json 会把 tuple/自定义类型打回原形）。"""
        clip = _video_clip()
        text = clip.to_json()
        again = Clip.from_dict(json.loads(text)).to_dict()
        self.assertEqual(clip.to_dict(), again)

    def test_nested_stats_not_double_passed(self):
        """回归：meta.stats 曾被展开后又显式传一次。"""
        d = _video_clip().to_dict()
        clip = Clip.from_dict(d)
        self.assertEqual(clip.meta.stats.like, 100)
        self.assertEqual(clip.meta.stats.collect, 20)

    def test_nested_keys_not_double_passed(self):
        """回归:meta/assets/content/provenance 曾在顶层重复传参。"""
        d = _video_clip().to_dict()
        clip = Clip.from_dict(d)  # 不抛 TypeError 即通过
        self.assertEqual(len(clip.assets), 2)
        self.assertEqual(len(clip.content.transcript), 2)
        self.assertEqual(clip.provenance.extractor, "asr:faster:medium")

    def test_missing_keys_fall_back_to_defaults(self):
        clip = Clip.from_dict({"id": "douyin:1", "platform": "douyin"})
        self.assertEqual(clip.meta.title, "")
        self.assertEqual(clip.meta.stats.like, 0)
        self.assertEqual(clip.assets, [])
        self.assertEqual(clip.content.transcript, [])

    def test_unknown_keys_ignored(self):
        """向前兼容：将来新增字段的旧文件不该读崩。"""
        d = _video_clip().to_dict()
        d["future_field"] = "x"
        d["meta"]["future_nested"] = "y"
        clip = Clip.from_dict(d)
        self.assertEqual(clip.meta.title, "测试标题")

    def test_topics_string_is_wrapped_into_list(self):
        """脏数据兜底：topics 被写成字符串时包成 list，避免下游逐字符遍历。"""
        clip = Clip.from_dict({"id": "douyin:1", "platform": "douyin", "meta": {"topics": "摄影"}})
        self.assertEqual(clip.meta.topics, ["摄影"])

    def test_save_and_load_file(self):
        clip = _video_clip()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "clip.json"
            clip.save(p)
            self.assertTrue(p.exists())
            self.assertEqual(Clip.load(p).to_dict(), clip.to_dict())


class TestValidation(unittest.TestCase):
    """validate() 只报结构问题，不报业务质量问题。"""

    def test_good_clip_has_no_problems(self):
        self.assertEqual(_video_clip().validate(), [])

    def test_id_without_separator(self):
        problems = _video_clip(id="7686034803698754161").validate()
        self.assertTrue(any("平台:原生id" in p for p in problems))

    def test_id_prefix_must_match_platform(self):
        problems = _video_clip(id="xiaohongshu:7686034803698754161").validate()
        self.assertTrue(any("不一致" in p for p in problems))

    def test_unknown_platform(self):
        problems = _video_clip().validate()
        self.assertEqual(problems, [])  # 基线
        bad = _video_clip(platform="bilibili", id="bilibili:1")
        self.assertTrue(any("未知平台" in p for p in bad.validate()))

    def test_bad_content_type(self):
        bad = _video_clip()
        bad.content_type = "podcast"
        self.assertTrue(any("content_type 非法" in p for p in bad.validate()))

    def test_empty_source_url(self):
        self.assertTrue(any("source_url 为空" for p in _video_clip(source_url="").validate()))

    def test_schema_version_mismatch(self):
        bad = _video_clip()
        bad.schema_version = "0.9"
        self.assertTrue(any("schema_version" in p for p in bad.validate()))

    def test_video_requires_transcript(self):
        bad = _video_clip()
        bad.content.transcript = []
        self.assertTrue(any("transcript 为空" in p for p in bad.validate()))

    def test_bad_asset_kind_and_role(self):
        bad = _video_clip()
        bad.assets[0].kind = "gif"
        bad.assets[1].role = "body"
        problems = bad.validate()
        self.assertTrue(any("asset.kind 非法" in p for p in problems))
        self.assertTrue(any("asset.role 非法" in p for p in problems))

    def test_empty_asset_path(self):
        bad = _video_clip()
        bad.assets[1].path = ""
        self.assertTrue(any("asset.path 为空" in p for p in bad.validate()))

    def test_image_text_with_images_but_no_ocr(self):
        bad = _image_text_clip()
        bad.content.images_ocr = []
        self.assertTrue(any("images_ocr 为空" in p for p in bad.validate()))

    def test_bad_ocr_status(self):
        bad = _image_text_clip()
        bad.content.images_ocr[0].status = "unknown"
        self.assertTrue(any("images_ocr.status 非法" in p for p in bad.validate()))

    def test_totally_empty_content(self):
        bad = Clip(id="douyin:1", platform="douyin", content_type="article", source_url="u")
        self.assertTrue(any("内容为空" in p for p in bad.validate()))

    def test_missing_required_fields_reported_not_crash(self):
        """回归：缺 content_type/source_url 曾是 TypeError 栈，用户看不出文件坏了。"""
        clip = Clip.from_dict({"id": "douyin:1"})
        problems = clip.validate()
        self.assertTrue(any("content_type" in p for p in problems))
        self.assertTrue(any("source_url" in p for p in problems))
        self.assertTrue(any("未知平台" in p for p in problems))

    def test_broken_asset_reported_not_crash(self):
        """回归：asset 缺 kind/path 曾是 TypeError 栈。"""
        clip = Clip.from_dict(
            {
                "id": "douyin:1",
                "platform": "douyin",
                "content_type": "video",
                "source_url": "u",
                "assets": [{}],
                "content": {"transcript": [{"text": "有内容"}]},
            }
        )
        problems = clip.validate()
        self.assertTrue(any("asset.kind" in p for p in problems))
        self.assertTrue(any("asset.path" in p for p in problems))


class TestOcrReady(unittest.TestCase):
    """ocr_ready 决定 publish 是否放行 —— 卡错了要么放脏数据进库，要么永远入不了库。"""

    def test_video_always_ready(self):
        self.assertTrue(_video_clip().ocr_ready())

    def test_image_text_ready_when_all_done(self):
        self.assertTrue(_image_text_clip("done").ocr_ready())

    def test_image_text_not_ready_when_pending(self):
        self.assertFalse(_image_text_clip("pending").ocr_ready())

    def test_image_text_not_ready_when_failed(self):
        self.assertFalse(_image_text_clip("failed").ocr_ready())

    def test_image_text_not_ready_when_no_ocr_items(self):
        """骨架刚产出、agent 还没读图时不得入库。"""
        clip = _image_text_clip()
        clip.content.images_ocr = []
        self.assertFalse(clip.ocr_ready())

    def test_pending_ocr_lists_unfinished(self):
        clip = _image_text_clip("pending")
        clip.content.images_ocr.append(schema.ImageOcr(order=2, text="x", status="done"))
        pending = clip.pending_ocr()
        self.assertEqual([o.order for o in pending], [1])


class TestConvenienceProperties(unittest.TestCase):

    def test_platform_id_strips_prefix(self):
        self.assertEqual(_video_clip().platform_id, "7686034803698754161")

    def test_platform_id_tolerates_missing_separator(self):
        clip = _video_clip()
        clip.id = "weird-id"
        self.assertEqual(clip.platform_id, "weird-id")

    def test_content_assets_excludes_cover(self):
        """封面混进正文图会让小红书笔记多出一张不该有的附件。"""
        clip = _image_text_clip()
        kinds = [a.kind for a in clip.content_assets]
        self.assertEqual(kinds, ["image"])

    def test_cover_found_by_role(self):
        self.assertIsNotNone(_video_clip().cover)

    def test_cover_found_by_kind(self):
        clip = _video_clip()
        clip.assets[0].role = "content"  # role 丢了，kind 仍是 cover
        self.assertIsNotNone(clip.cover)

    def test_no_cover_returns_none(self):
        clip = _video_clip()
        clip.assets = [a for a in clip.assets if a.kind != "cover"]
        self.assertIsNone(clip.cover)

    def test_full_text_joins_all_three_sections(self):
        clip = _image_text_clip()
        text = clip.full_text()
        self.assertIn("整理出这份清单。", text)
        self.assertIn("车辆应急", text)

    def test_full_text_includes_transcript_for_video(self):
        self.assertIn("出门拍了2000张照片", _video_clip().full_text())

    def test_full_text_can_exclude_transcript(self):
        clip = _video_clip()
        clip.content.text_blocks = [schema.TextBlock(text="只有这段")]
        self.assertEqual(clip.full_text(include_transcript=False), "只有这段")

    def test_full_text_empty_when_nothing(self):
        clip = Clip(id="douyin:1", platform="douyin", content_type="article", source_url="u")
        self.assertEqual(clip.full_text(), "")


class TestNewClip(unittest.TestCase):

    def test_new_clip_builds_id_and_defaults(self):
        clip = schema.new_clip(
            platform="douyin", platform_id="123", content_type="video", source_url="https://x"
        )
        self.assertEqual(clip.id, "douyin:123")
        self.assertEqual(clip.schema_version, schema.SCHEMA_VERSION)

    def test_new_clip_canonical_url_defaults_to_source(self):
        clip = schema.new_clip(
            platform="douyin", platform_id="1", content_type="video", source_url="https://x"
        )
        self.assertEqual(clip.canonical_url, "https://x")


if __name__ == "__main__":
    unittest.main()
