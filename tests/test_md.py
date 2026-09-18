"""md 组装测试（L2 的交付形态）。

这份 md 是「入库 agent 唯一读的东西」，格式错了会一路错到知识库里：

- 来源块必须能被解析出来（键值行、跳过空值），且**不能出现 unknown 这类脏值**
- 正文**不能带 frontmatter**（属性由配套 skill 组装）
- 文件不能有空首行（会让 agent 的解析与人工阅读都别扭）
"""

import unittest

from l2 import md


def _job(**overrides) -> dict:
    job = {
        "id": "1a2b3c4d5e6f",
        "type": "video",
        "material": "/abs/video.mp4",
        "images": [],
        "platform": "bilibili",
        "native_id": "BV1xx411c7mD",
        "meta": {
            "title": "认识 MAF",
            "source_url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "author": "某UP主",
            "published": "2026-09-01",
            "description": "",
        },
        "warnings": [],
    }
    job.update(overrides)
    return job


class TestClippingType(unittest.TestCase):

    def test_known_platform_and_kind(self):
        self.assertEqual(md.clipping_type(_job()), "bilibili-video")

    def test_xiaohongshu_image_set(self):
        job = _job(platform="xiaohongshu", type="image_set")
        self.assertEqual(md.clipping_type(job), "xiaohongshu-note")

    def test_unknown_platform_falls_back_to_local(self):
        job = _job(platform="", type="audio")
        self.assertEqual(md.clipping_type(job), "local-audio")

    def test_known_platform_unknown_kind(self):
        self.assertEqual(md.clipping_type(_job(type="audio")), "bilibili-audio")


class TestSourceBlock(unittest.TestCase):

    def test_contains_expected_keys(self):
        block = md.source_block(_job(), extractor="whisper:mlx:large-v3")
        for key in ("title:", "source_url:", "author:", "published:",
                    "clipping_type:", "platform:", "native_id:", "extractor:"):
            self.assertIn(key, block, key)

    def test_empty_values_are_skipped(self):
        block = md.source_block(_job(platform="", native_id=""))
        self.assertNotIn("platform:", block)
        self.assertNotIn("native_id:", block)
        self.assertNotIn("extractor:", block)

    def test_never_writes_placeholder_junk(self):
        job = _job(platform="", native_id="")
        job["meta"]["author"] = ""
        block = md.source_block(job)
        self.assertNotIn("unknown", block.lower())

    def test_multiline_value_flattened(self):
        job = _job()
        job["meta"]["title"] = "第一行\n第二行"
        block = md.source_block(job)
        title_lines = [ln for ln in block.splitlines() if ln.startswith("title:")]
        self.assertEqual(len(title_lines), 1)
        self.assertEqual(title_lines[0], "title: 第一行 第二行")

    def test_delimited_by_html_comment(self):
        block = md.source_block(_job())
        self.assertTrue(block.startswith(md.META_OPEN))
        self.assertTrue(block.endswith(md.META_CLOSE))


class TestTranscriptBody(unittest.TestCase):

    def test_short_segments_merge_into_one_paragraph(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "第一句。"},
            {"start": 1.0, "end": 2.0, "text": "第二句。"},
        ]
        body = md.transcript_body(segments)
        self.assertIn("## 转写全文", body)
        self.assertEqual(body.count("**[00:00]**"), 1)   # 合并成一段，只标一次时间
        self.assertIn("第一句。第二句。", body)

    def test_long_text_splits_into_paragraphs(self):
        segments = [{"start": float(i), "end": float(i + 1), "text": "很长的句子内容" * 8}
                    for i in range(4)]
        body = md.transcript_body(segments)
        self.assertGreater(body.count("**[00:"), 1)

    def test_hour_plus_uses_hhmmss(self):
        """超过 1 小时要带上小时位，否则 `61:40` 这种写法会让人误读。"""
        body = md.transcript_body([{"start": 3700.0, "end": 3701.0, "text": "一小时以后。"}])
        self.assertIn("**[01:01:40]**", body)

    def test_empty_segments_render_placeholder(self):
        body = md.transcript_body([])
        self.assertIn("没有识别到内容", body)


class TestImageBody(unittest.TestCase):

    def test_each_image_gets_placeholder(self):
        body = md.image_body(["/work/a/001_1.jpg", "/work/a/002_2.jpg"])
        self.assertIn("<!--img: /work/a/001_1.jpg-->", body)
        self.assertIn("<!--img: /work/a/002_2.jpg-->", body)
        self.assertIn("**图 1**：", body)
        self.assertIn("**图 2**：", body)

    def test_mentions_agent_task(self):
        self.assertIn("agent", md.image_body(["/a.jpg"]))


class TestRender(unittest.TestCase):

    def test_starts_with_source_block_no_leading_blank(self):
        text = md.render(_job(), md.transcript_body([{"start": 0.0, "text": "内容。"}]))
        self.assertTrue(text.startswith(md.META_OPEN))

    def test_no_frontmatter_markers(self):
        """正文里出现 `---` 分隔线会被误当成 frontmatter。"""
        text = md.render(_job(), md.image_body(["/a.jpg"]))
        stripped = "\n".join(
            line for line in text.splitlines() if not line.startswith(md.META_OPEN)
        )
        self.assertNotIn("\n---\n", stripped)

    def test_warnings_render_as_callout(self):
        text = md.render(
            _job(), md.transcript_body([{"start": 0.0, "text": "内容。"}]),
            warnings=["第一条警告", "第二条警告"],
        )
        self.assertIn("> [!warning] 第一条警告", text)
        self.assertIn("> 第二条警告", text)

    def test_blank_warnings_ignored(self):
        text = md.render(_job(), "正文", warnings=["", "  "])
        self.assertNotIn("[!warning]", text)

    def test_single_trailing_newline(self):
        text = md.render(_job(), "正文\n\n")
        self.assertTrue(text.endswith("正文\n"))

    def test_write_creates_file(self):
        path = md.write("item123", "内容")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "内容")


if __name__ == "__main__":
    unittest.main()
