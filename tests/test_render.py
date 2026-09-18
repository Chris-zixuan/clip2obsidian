"""入库渲染层测试（L4）。

这一层是项目存在的理由 —— 前 90% 都能借现成工具，只有「落成符合个人知识库
规范的笔记」必须自己写。所以这里的用例集中在两处最容易出事故的地方：

1. **YAML frontmatter 的完整性** —— description 里混进真实换行符就会把
   frontmatter 撑破，整篇笔记的属性全部失效（真实踩过）。
2. **description 的取值口径** —— 取 ASR 转写会把同音字错误写进属性面板
   （真实踩过：「RAW 原片」→「REW圆片」、「手选」→「首选」）。
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import config as config_mod
from core import schema
from publish import render as R


def _cfg(vault: str, *, max_len: int = 60, desc_source: str = "auto") -> config_mod.Config:
    """指向临时目录的配置，绝不碰真实知识库。"""
    cfg = config_mod.Config()
    cfg.vault = config_mod.VaultConfig(
        path=vault, inbox_subdir="0_Inbox/Clippings", attachment_subdir="8_附件"
    )
    cfg.publish.filename_max_len = max_len
    cfg.publish.description_source = desc_source
    cfg.publish.verify_after_publish = False  # 不跑 obsidian CLI
    return cfg


def _clip(
    *,
    title: str = "测试标题",
    author: str = "某UP主",
    published: str = "2026-09-16",
    description: str = "",
    transcript: str = "",
) -> schema.Clip:
    clip = schema.new_clip(
        platform="bilibili",
        platform_id="BV1xx411c7mD",
        content_type="video",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    clip.meta.title = title
    clip.meta.author = author
    clip.meta.published = published
    clip.meta.description = description
    clip.provenance.fetcher = "local-import"
    clip.provenance.extractor = "asr:faster:medium"
    if transcript:
        clip.content.transcript = [schema.Segment(start=0.0, end=2.0, text=transcript)]
    return clip


class TestYamlEscape(unittest.TestCase):
    """frontmatter 被撑破是静默故障 —— Obsidian 会整段属性读不出来。"""

    def test_newline_collapsed(self):
        self.assertEqual(R._yaml_escape("第一行\n第二行"), "第一行 第二行")

    def test_leading_trailing_whitespace_around_newline_collapsed(self):
        self.assertEqual(R._yaml_escape("a  \n\t b"), "a b")

    def test_double_quote_escaped(self):
        self.assertEqual(R._yaml_escape('他说"你好"'), '他说\\"你好\\"')

    def test_backslash_escaped(self):
        self.assertEqual(R._yaml_escape(r"C:\path"), r"C:\\path")

    def test_none_safe(self):
        self.assertEqual(R._yaml_escape(None), "")

    def test_result_never_contains_raw_newline(self):
        for raw in ["a\nb", "a\r\nb", "a\rb", "开头\n\n结尾", "  \n  "]:
            self.assertNotIn("\n", R._yaml_escape(raw))


class TestFrontmatter(unittest.TestCase):
    """来源层字段必须与既有 importer 剪藏一致。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cfg = _cfg(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _fm(self, clip, title="测试标题", tags=("生活",)):
        return R._frontmatter(clip, title, list(tags), self.cfg)

    def test_required_fields_present(self):
        fm = self._fm(_clip())
        for key in ("类型:", "title:", "source:", "author:", "clipping_type:", "created:", "tags:"):
            self.assertIn(key, fm)

    def test_clipping_type_matches_platform(self):
        self.assertIn("clipping_type: bilibili-video", self._fm(_clip()))

    def test_created_is_today(self):
        self.assertIn(f"created: {date.today().isoformat()}", self._fm(_clip()))

    def test_published_is_bare_value(self):
        """date 类型加引号会被 Obsidian 属性面板改回去。"""
        self.assertIn("published: 2026-09-16", self._fm(_clip()))
        self.assertNotIn('published: "2026-09-16"', self._fm(_clip()))

    def test_published_omitted_when_unknown(self):
        self.assertNotIn("published:", self._fm(_clip(published="")))

    def test_author_rendered_as_wikilink(self):
        self.assertIn('- "[[某UP主]]"', self._fm(_clip()))

    def test_missing_author_omitted(self):
        """作者为空时不写 author 行，绝不写 [[unknown]] 脏值。"""
        self.assertNotIn("author:", self._fm(_clip(author="")))
        self.assertNotIn("[[unknown]]", self._fm(_clip(author="")))

    def test_missing_source_omitted(self):
        """source_url 为空时不写 source 行。"""
        clip = _clip()
        clip.source_url = ""
        clip.canonical_url = ""
        self.assertNotIn("source:", self._fm(clip))

    def test_multiline_description_does_not_break_yaml(self):
        """回归：description 里落进真实换行符会把 frontmatter 撑破。"""
        clip = _clip(description="第一行\n第二行\n第三行")
        fm = self._fm(clip)
        desc_lines = [ln for ln in fm.split("\n") if ln.startswith("description:")]
        self.assertEqual(len(desc_lines), 1)
        self.assertTrue(desc_lines[0].startswith('description: "'))
        self.assertTrue(desc_lines[0].endswith('"'))

    def test_every_line_is_single_line_yaml(self):
        fm = self._fm(_clip(description="带\n换行"))
        self.assertNotIn("\n\n", fm)          # 没有空行被意外插入
        self.assertTrue(fm.startswith("---\n"))
        self.assertTrue(fm.endswith("\n---"))

    def test_tags_indented_as_yaml_list(self):
        fm = self._fm(_clip(), tags=("生活", "成长"))
        self.assertIn("tags:\n  - 生活\n  - 成长", fm)

    def test_tags_not_carry_clippings(self):
        """tags 只用受控词表标签，不写 clippings。"""
        self.assertNotIn("- clippings", self._fm(_clip(), tags=("生活",)))


class TestCleanPlatformText(unittest.TestCase):

    def test_strips_hashtags(self):
        self.assertEqual(R._clean_platform_text("好内容 #摄影 #AI选片"), "好内容")

    def test_strips_client_junk_tail(self):
        got = R._clean_platform_text("正常文案内容……版本过低，升级后可展示全部信息")
        self.assertEqual(got, "正常文案内容")

    def test_collapses_newlines(self):
        self.assertEqual(R._clean_platform_text("第一行\n第二行"), "第一行 第二行")

    def test_keeps_ellipsis_without_junk_keyword(self):
        got = R._clean_platform_text("文案里正常出现省略号……但后面没有关键词")
        self.assertIn("省略号", got)

    def test_empty_safe(self):
        self.assertEqual(R._clean_platform_text(""), "")
        self.assertEqual(R._clean_platform_text(None), "")


class TestDescriptionSource(unittest.TestCase):
    """默认口径：取平台文案，不取 ASR 转写。"""

    LONG_PLATFORM = "出门拍了2000张照片，用AI五分钟选完连拍相似组，这个工具真的别再熬夜手选了。"
    TRANSCRIPT = "出门拍了2000张照片,用AI五分钟选择,REW圆片也能直接筛。"

    def test_auto_prefers_platform_text(self):
        clip = _clip(description=self.LONG_PLATFORM, transcript=self.TRANSCRIPT)
        got = R._description_source(clip, "auto")
        self.assertEqual(got, self.LONG_PLATFORM)
        # 核心：不得把 ASR 的同音字错误写进属性面板
        self.assertNotIn("REW圆片", got)

    def test_auto_falls_back_to_transcript_when_platform_too_short(self):
        clip = _clip(description="短", transcript=self.TRANSCRIPT)
        self.assertIn("REW圆片", R._description_source(clip, "auto"))

    def test_auto_falls_back_when_platform_only_has_hashtags(self):
        clip = _clip(description="#摄影 #AI选片", transcript=self.TRANSCRIPT)
        self.assertIn("REW圆片", R._description_source(clip, "auto"))

    def test_platform_mode_ignores_threshold(self):
        clip = _clip(description="短文案", transcript=self.TRANSCRIPT)
        self.assertEqual(R._description_source(clip, "platform"), "短文案")

    def test_transcript_mode_uses_transcript(self):
        clip = _clip(description=self.LONG_PLATFORM, transcript=self.TRANSCRIPT)
        self.assertIn("REW圆片", R._description_source(clip, "transcript"))

    def test_result_is_flattened(self):
        clip = _clip(description="第一行\n第二行，这是一段足够长的平台文案内容")
        self.assertNotIn("\n", R._description_source(clip, "auto"))


class TestCleanTitle(unittest.TestCase):
    """标题常被截断，留下 `...` 尾巴（真实样本）。"""

    REAL = (
        "摄影师不用熬夜选片了，5分钟选完 RAW 原片直接筛，连拍自动归进相似组 "
        "AI 先粗筛，最后留哪张还是摄影师自己决定。这样的工具真的别再熬夜手选了..."
    )

    def test_strips_truncation_tail(self):
        got = R._clean_title(self.REAL)
        self.assertNotIn("...", got)
        self.assertNotIn("别再熬夜手选", got)
        self.assertTrue(got.startswith("摄影师不用熬夜选片了"))

    def test_strips_cjk_ellipsis_tail(self):
        self.assertNotIn("…", R._clean_title("一个标题这么写然后被截断了…"))

    def test_plain_title_untouched(self):
        self.assertEqual(R._clean_title("正常标题"), "正常标题")

    def test_empty_falls_back_to_untitled(self):
        self.assertEqual(R._clean_title(""), "untitled")
        self.assertEqual(R._clean_title(None), "untitled")


class TestSanitize(unittest.TestCase):

    def test_removes_filesystem_illegal_chars(self):
        self.assertEqual(R._sanitize('a/b:c*d?e"f<g>h|i#j[k]l'), "abcdefghijkl")

    def test_truncates_to_limit(self):
        self.assertEqual(len(R._sanitize("字" * 100, 20)), 20)

    def test_strips_surrounding_dots(self):
        self.assertEqual(R._sanitize("...名字..."), "名字")

    def test_empty_falls_back(self):
        self.assertEqual(R._sanitize(""), "untitled")
        self.assertEqual(R._sanitize("///"), "untitled")


class TestRenderGuards(unittest.TestCase):
    """入库前的闸门。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cfg = _cfg(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_rejects_without_tags(self):
        with self.assertRaises(R.PublishError) as ctx:
            R.render(_clip(transcript="内容足够长可以作为正文使用"), tags=[], cfg=self.cfg)
        self.assertIn("标签", str(ctx.exception))

    def test_rejects_invalid_clip(self):
        bad = _clip(transcript="内容")
        bad.content.transcript = []
        bad.assets = []
        with self.assertRaises(R.PublishError) as ctx:
            R.render(bad, tags=["生活"], cfg=self.cfg)
        self.assertIn("校验未通过", str(ctx.exception))


class TestRenderOutput(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cfg = _cfg(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_dry_run_writes_nothing(self):
        clip = _clip(transcript="出门拍了2000张照片，用AI五分钟选完。")
        res = R.render(clip, tags=["生活"], title="测试", cfg=self.cfg, dry_run=True)
        self.assertFalse(res.path.exists())
        self.assertFalse(res.path.parent.exists())

    def test_writes_note_into_inbox(self):
        clip = _clip(transcript="出门拍了2000张照片，用AI五分钟选完。")
        res = R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        self.assertTrue(res.path.exists())
        self.assertEqual(res.path.parent.name, "Clippings")

    def test_note_starts_with_frontmatter(self):
        clip = _clip(transcript="正文内容足够长。")
        res = R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        text = res.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("类型: clippings", text)

    def test_digest_inserted_before_transcript(self):
        clip = _clip(transcript="出门拍了2000张照片，用AI五分钟选完。")
        res = R.render(
            clip, tags=["生活"], title="测试标题", digest="## 要点\n\n- 直吃 RAW", cfg=self.cfg
        )
        text = res.path.read_text(encoding="utf-8")
        self.assertIn("## 要点", text)
        self.assertLess(text.index("## 要点"), text.index("## 转写全文"))

    def test_video_body_has_timestamped_transcript(self):
        clip = _clip(transcript="出门拍了2000张照片，用AI五分钟选完，真的不用熬夜手选了。")
        res = R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        text = res.path.read_text(encoding="utf-8")
        self.assertIn("## 转写全文", text)
        self.assertIn("**[00:00]**", text)

    def test_warning_callout_rendered_from_provenance(self):
        clip = _clip(transcript="正文内容足够长。")
        clip.provenance.warnings = ["转写由语音识别生成，可能有同音字错误。"]
        res = R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        self.assertIn("> [!warning]", res.path.read_text(encoding="utf-8"))

    def test_warning_omitted_when_none(self):
        clip = _clip(transcript="正文内容足够长。")
        res = R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        self.assertNotIn("> [!warning]", res.path.read_text(encoding="utf-8"))

    def test_title_argument_overrides_meta_title(self):
        clip = _clip(title="很长的原始标题...", transcript="正文内容足够长。")
        res = R.render(clip, tags=["生活"], title="精简标题", cfg=self.cfg)
        self.assertEqual(res.note_name, "精简标题")

    def test_filename_truncated_to_config_limit(self):
        cfg = _cfg(self._td.name, max_len=10)
        clip = _clip(transcript="正文内容足够长。")
        res = R.render(clip, tags=["生活"], title="很" * 50, cfg=cfg)
        self.assertEqual(len(res.note_name), 10)

    def test_rerender_backs_up_previous_note(self):
        clip = _clip(transcript="正文内容足够长。")
        R.render(clip, tags=["生活"], title="测试标题", cfg=self.cfg)
        second = R.render(clip, tags=["成长"], title="测试标题", cfg=self.cfg)
        self.assertIsNotNone(second.replaced_backup)
        self.assertTrue(second.replaced_backup.exists())
        self.addCleanup(lambda: second.replaced_backup.unlink(missing_ok=True))
        self.assertIn("成长", second.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
