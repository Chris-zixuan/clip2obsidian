"""clip.json 契约层测试。

这是四层之间唯一的通信契约，一旦序列化/校验出问题，所有平台同时失效 ——
所以这里覆盖的是**结构性回归**，不是业务质量。

其中 test_roundtrip_* 与 test_nested_* 是真实 bug 的回归测试：`from_dict`
曾把 `meta` / `assets` / `content` / `provenance` 这些嵌套键既通过 `_pick`
展开、又显式传一次，导致 `TypeError: got multiple values for keyword
argument` —— 所有 clip.json 都读不回来。
"""

import json
import tempfile
import unittest
from pathlib import Path

from core import schema
from core.schema import Clip, Meta, Stats


def _clip(**overrides) -> Clip:
    """一个结构完整、能通过校验的 video clip。"""
    base = dict(
        id="bilibili:BV1xx411c7mD",
        platform="bilibili",
        content_type="video",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        canonical_url="https://www.bilibili.com/video/BV1xx411c7mD",
        fetched_at="2026-09-17T11:20:00+08:00",
        meta=Meta(
            title="测试标题",
            author="某UP主",
            published="2026-09-16",
            duration_sec=61,
            stats=Stats(like=100, collect=20, comment=3, share=1),
            topics=["摄影", "AI选片"],
            description="出门拍了2000张照片，用AI五分钟选完。",
        ),
        assets=[schema.Asset(kind="video", path="/abs/media.mp4", order=0)],
        content=schema.Content(
            transcript=[
                schema.Segment(start=0.0, end=3.2, text="出门拍了2000张照片，"),
                schema.Segment(start=3.2, end=6.4, text="用AI五分钟选完。"),
            ],
        ),
        provenance=schema.Provenance(
            fetcher="local-import",
            extractor="asr:faster:medium",
            warnings=["转写由语音识别生成，可能有同音字错误。"],
        ),
    )
    base.update(overrides)
    return Clip(**base)


class TestSerializationByRoundTrip(unittest.TestCase):
    """to_dict → from_dict → to_dict 必须完全一致。"""

    def test_roundtrip_clip(self):
        clip = _clip()
        first = clip.to_dict()
        again = Clip.from_dict(first).to_dict()
        self.assertEqual(first, again)

    def test_roundtrip_survives_json(self):
        """经真实 JSON 文本走一遍（json 会把 tuple/自定义类型打回原形）。"""
        clip = _clip()
        again = Clip.from_dict(json.loads(clip.to_json())).to_dict()
        self.assertEqual(clip.to_dict(), again)

    def test_nested_stats_not_double_passed(self):
        """回归：meta.stats 曾被展开后又显式传一次。"""
        clip = Clip.from_dict(_clip().to_dict())
        self.assertEqual(clip.meta.stats.like, 100)
        self.assertEqual(clip.meta.stats.collect, 20)

    def test_nested_keys_not_double_passed(self):
        """回归：meta/assets/content/provenance 曾在顶层重复传参。"""
        d = _clip().to_dict()
        clip = Clip.from_dict(d)  # 不抛 TypeError 即通过
        self.assertEqual(len(clip.assets), 1)
        self.assertEqual(len(clip.content.transcript), 2)
        self.assertEqual(clip.provenance.extractor, "asr:faster:medium")

    def test_missing_keys_fall_back_to_defaults(self):
        clip = Clip.from_dict({"id": "bilibili:1", "platform": "bilibili"})
        self.assertEqual(clip.meta.title, "")
        self.assertEqual(clip.meta.stats.like, 0)
        self.assertEqual(clip.assets, [])
        self.assertEqual(clip.content.transcript, [])

    def test_unknown_keys_ignored(self):
        """向前兼容：将来新增字段的旧文件不该读崩。"""
        d = _clip().to_dict()
        d["future_field"] = "x"
        d["meta"]["future_nested"] = "y"
        self.assertEqual(Clip.from_dict(d).meta.title, "测试标题")

    def test_topics_string_is_wrapped_into_list(self):
        """脏数据兜底：topics 被写成字符串时包成 list，避免下游逐字符遍历。"""
        clip = Clip.from_dict(
            {"id": "bilibili:1", "platform": "bilibili", "meta": {"topics": "摄影"}}
        )
        self.assertEqual(clip.meta.topics, ["摄影"])

    def test_save_and_load_file(self):
        clip = _clip()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "clip.json"
            clip.save(p)
            self.assertTrue(p.exists())
            self.assertEqual(Clip.load(p).to_dict(), clip.to_dict())


class TestValidation(unittest.TestCase):
    """validate() 只报结构问题，不报业务质量问题。"""

    def test_good_clip_has_no_problems(self):
        self.assertEqual(_clip().validate(), [])

    def test_id_without_separator(self):
        problems = _clip(id="BV1xx411c7mD").validate()
        self.assertTrue(any("平台:原生id" in p for p in problems))

    def test_id_prefix_must_match_platform(self):
        problems = _clip(id="youtube:BV1xx411c7mD").validate()
        self.assertTrue(any("不一致" in p for p in problems))

    def test_unknown_platform(self):
        bad = _clip(platform="tiktok", id="tiktok:1")
        self.assertTrue(any("未知平台" in p for p in bad.validate()))

    def test_bad_content_type(self):
        bad = _clip()
        bad.content_type = "podcast"
        self.assertTrue(any("content_type 非法" in p for p in bad.validate()))

    def test_empty_source_url_is_allowed(self):
        """本地导入没有链接时 source_url 允许为空，不报结构错误。"""
        self.assertFalse(any("source_url" in p for p in _clip(source_url="").validate()))

    def test_schema_version_mismatch(self):
        bad = _clip()
        bad.schema_version = "0.9"
        self.assertTrue(any("schema_version" in p for p in bad.validate()))

    def test_video_requires_transcript(self):
        bad = _clip()
        bad.content.transcript = []
        self.assertTrue(any("transcript 为空" in p for p in bad.validate()))

    def test_bad_asset_kind(self):
        bad = _clip()
        bad.assets[0].kind = "gif"
        self.assertTrue(any("asset.kind 非法" in p for p in bad.validate()))

    def test_empty_asset_path(self):
        bad = _clip()
        bad.assets[0].path = ""
        self.assertTrue(any("asset.path 为空" in p for p in bad.validate()))

    def test_totally_empty_content(self):
        bad = Clip(id="bilibili:1", platform="bilibili", content_type="video", source_url="u")
        self.assertTrue(any("内容为空" in p for p in bad.validate()))

    def test_missing_required_fields_reported_not_crash(self):
        """回归：缺 content_type 曾是 TypeError 栈，用户看不出文件坏了。"""
        clip = Clip.from_dict({"id": "bilibili:1"})
        problems = clip.validate()
        self.assertTrue(any("content_type" in p for p in problems))
        self.assertTrue(any("未知平台" in p for p in problems))

    def test_broken_asset_reported_not_crash(self):
        """回归：asset 缺 kind/path 曾是 TypeError 栈。"""
        clip = Clip.from_dict(
            {
                "id": "bilibili:1",
                "platform": "bilibili",
                "content_type": "video",
                "source_url": "u",
                "assets": [{}],
                "content": {"transcript": [{"text": "有内容"}]},
            }
        )
        problems = clip.validate()
        self.assertTrue(any("asset.kind" in p for p in problems))
        self.assertTrue(any("asset.path" in p for p in problems))


class TestConvenienceProperties(unittest.TestCase):

    def test_platform_id_strips_prefix(self):
        self.assertEqual(_clip().platform_id, "BV1xx411c7mD")

    def test_platform_id_tolerates_missing_separator(self):
        clip = _clip()
        clip.id = "weird-id"
        self.assertEqual(clip.platform_id, "weird-id")

    def test_full_text_joins_transcript(self):
        self.assertIn("出门拍了2000张照片", _clip().full_text())

    def test_full_text_empty_when_nothing(self):
        clip = Clip(id="bilibili:1", platform="bilibili", content_type="video", source_url="u")
        self.assertEqual(clip.full_text(), "")


class TestNewClip(unittest.TestCase):

    def test_new_clip_builds_id_and_defaults(self):
        clip = schema.new_clip(
            platform="bilibili", platform_id="BV1", content_type="video", source_url="https://x"
        )
        self.assertEqual(clip.id, "bilibili:BV1")
        self.assertEqual(clip.schema_version, schema.SCHEMA_VERSION)

    def test_new_clip_canonical_url_defaults_to_source(self):
        clip = schema.new_clip(
            platform="bilibili", platform_id="BV1", content_type="video", source_url="https://x"
        )
        self.assertEqual(clip.canonical_url, "https://x")


if __name__ == "__main__":
    unittest.main()
