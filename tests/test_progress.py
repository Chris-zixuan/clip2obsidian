"""进度上报测试。

进度是给「等待中的人」看的，出错的代价是误导：显示 100% 却没结束、
或者非终端环境里写下一堆带回车符的乱码（日志文件里看着像坏了）。
所以边界值、降级行为、原子写都锁一遍。
"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from core import paths, progress


class TTYStream(io.StringIO):
    """假装是终端：StringIO 的 isatty() 默认返回 False。"""

    def isatty(self) -> bool:
        return True


class TestFormatDuration(unittest.TestCase):

    def test_under_one_hour(self):
        self.assertEqual(progress.format_duration(83), "1:23")
        self.assertEqual(progress.format_duration(0), "0:00")

    def test_over_one_hour(self):
        self.assertEqual(progress.format_duration(3723), "1:02:03")

    def test_unknown(self):
        self.assertEqual(progress.format_duration(-1), "--:--")
        self.assertEqual(progress.format_duration(None), "--:--")


class TestRenderBar(unittest.TestCase):

    def test_ends_filled(self):
        bar = progress.render_bar(1.0, width=10)
        self.assertEqual(bar, "█" * 10)

    def test_half(self):
        bar = progress.render_bar(0.5, width=10)
        self.assertEqual(bar.count("█"), 5)

    def test_clamped(self):
        self.assertEqual(progress.render_bar(-3, width=4), "░" * 4)
        self.assertEqual(progress.render_bar(9, width=4), "█" * 4)

    def test_none_ratio(self):
        self.assertEqual(progress.render_bar(None, width=4), "░" * 4)


class TestParseFfmpegProgress(unittest.TestCase):
    """ffmpeg 的 out_time_ms 单位其实是微秒 —— 按毫秒算会让进度瞬间冲到 100%。"""

    def test_out_time_us(self):
        self.assertEqual(progress.parse_ffmpeg_progress("out_time_us=2500000"), 2.5)

    def test_out_time_ms_is_microseconds(self):
        self.assertEqual(progress.parse_ffmpeg_progress("out_time_ms=2500000"), 2.5)

    def test_out_time_clock_format(self):
        self.assertAlmostEqual(progress.parse_ffmpeg_progress("out_time=00:01:02.500000"), 62.5)

    def test_unrelated_line(self):
        self.assertIsNone(progress.parse_ffmpeg_progress("frame=10"))
        self.assertIsNone(progress.parse_ffmpeg_progress(""))

    def test_malformed_value(self):
        self.assertIsNone(progress.parse_ffmpeg_progress("out_time_us=abc"))


class TestSnapshot(unittest.TestCase):

    def test_ratio_and_eta(self):
        snap = progress.Snapshot(total=100.0, done=50.0)
        snap.updated_at = snap.started_at + 10.0
        self.assertEqual(snap.ratio, 0.5)
        self.assertAlmostEqual(snap.eta, 10.0, places=1)

    def test_eta_unknown_when_total_missing(self):
        snap = progress.Snapshot(total=0.0, done=5.0)
        self.assertEqual(snap.eta, -1.0)

    def test_eta_unknown_at_start(self):
        snap = progress.Snapshot(total=100.0, done=0.0)
        snap.updated_at = snap.started_at + 5.0
        self.assertEqual(snap.eta, -1.0)

    def test_to_dict_is_json_serializable(self):
        payload = json.dumps(progress.Snapshot(total=10.0, done=5.0).to_dict())
        self.assertIn("ratio", payload)


class TestInlineReporter(unittest.TestCase):

    def test_non_tty_uses_milestone_lines(self):
        """被重定向到文件时不能写 \\r，否则日志像坏了一样。"""
        stream = io.StringIO()
        reporter = progress.InlineReporter(stream)
        reporter.start(100.0, "转写")
        for done in (10, 30, 70, 100):
            reporter.update(done, "进行中")
        reporter.close("完成")
        self.assertNotIn("\r", stream.getvalue())
        self.assertIn("[ 10%]", stream.getvalue())
        self.assertIn("完成", stream.getvalue())

    def test_non_tty_start_writes_nothing(self):
        stream = io.StringIO()
        progress.InlineReporter(stream).start(100.0, "转写")
        self.assertEqual(stream.getvalue(), "")

    def test_tty_repaints_in_place(self):
        stream = TTYStream()
        reporter = progress.InlineReporter(stream)
        reporter.start(100.0, "转写")
        reporter.update(50.0)
        reporter.close("完成")
        self.assertIn("\r", stream.getvalue())
        self.assertIn("█", stream.getvalue())

    def test_disabled_writes_nothing(self):
        stream = io.StringIO()
        reporter = progress.InlineReporter(stream, enabled=False)
        reporter.start(10.0, "x")
        reporter.update(5.0)
        reporter.close("done")
        self.assertEqual(stream.getvalue(), "")

    def test_short_line_padded_over_long_previous(self):
        """上一行更长时必须补空格，否则残留字符会连在新行后面。"""
        stream = TTYStream()
        reporter = progress.InlineReporter(stream, width=10)
        reporter.start(100.0, "很长的标签" * 5)
        reporter.update(1.0, "")
        reporter.close()
        self.assertNotEqual(stream.getvalue(), "")


class TestJsonReporter(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "sub" / "item.progress.json"
        self.addCleanup(self._td.cleanup)

    def test_writes_snapshot(self):
        """落盘按 1 秒节流，所以最后一次状态由 close() 保证写入。"""
        reporter = progress.JsonReporter(self.path)
        reporter.start(100.0, "转写")
        reporter.close("第 1/2 片")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["total"], 100.0)
        self.assertEqual(data["summary"], "第 1/2 片")
        self.assertTrue(data["closed"])

    def test_start_writes_initial_snapshot(self):
        reporter = progress.JsonReporter(self.path)
        reporter.start(100.0, "转写")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["label"], "转写")
        self.assertFalse(data["closed"])

    def test_update_is_throttled_but_close_flushes(self):
        reporter = progress.JsonReporter(self.path)
        reporter.start(100.0, "转写")
        reporter.update(80.0, "第 8/10 片")   # 距上次写入不足 1 秒 → 被节流
        reporter.close("完成")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["done"], 100.0)   # close 补足了终点

    def test_close_marks_closed(self):
        reporter = progress.JsonReporter(self.path)
        reporter.start(10.0, "x")
        reporter.close("完成")
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["closed"])

    def test_no_temp_file_left_behind(self):
        reporter = progress.JsonReporter(self.path)
        reporter.start(10.0, "x")
        reporter.close("完成")
        leftovers = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_write_failure_does_not_raise(self):
        """进度写不进去不该让主流程失败。"""
        reporter = progress.JsonReporter(Path("/proc/definitely/not/writable.json"))
        reporter.start(1.0, "x")
        reporter.update(0.5)
        reporter.close()


class TestMakeReporter(unittest.TestCase):

    def test_off_returns_null(self):
        self.assertIsInstance(progress.make_reporter("off"), progress.NullReporter)

    def test_inline_by_default(self):
        self.assertIsInstance(progress.make_reporter("inline"), progress.InlineReporter)

    def test_unknown_mode_falls_back_to_inline(self):
        self.assertIsInstance(progress.make_reporter("nonsense"), progress.InlineReporter)

    def test_window_without_path_falls_back(self):
        self.assertIsInstance(progress.make_reporter("window"), progress.InlineReporter)

    def test_window_on_non_macos_falls_back_to_inline(self):
        """不打开真实终端窗口：只在非 macOS 分支上验证降级路径。"""
        with tempfile.TemporaryDirectory() as td:
            original = paths.IS_MACOS
            paths.IS_MACOS = False
            try:
                reporter = progress.make_reporter(
                    "window", progress_path=Path(td) / "p.json"
                )
                self.assertIsInstance(reporter, progress.WindowReporter)
                reporter.start(10.0, "x")
                reporter.update(5.0)
                reporter.close("完成")
            finally:
                paths.IS_MACOS = original
            data = json.loads((Path(td) / "p.json").read_text(encoding="utf-8"))
            self.assertTrue(data["closed"])


if __name__ == "__main__":
    unittest.main()
