"""转写层测试（provider 协议、降级、分片规划、缓存）。

全部离线：真引擎只做「是否安装」的探测，实际转写用注入的假 provider 验证。
这样测试既快，也不会因为某台机器没装模型而失败。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

from core import config as config_mod
from core import paths
from l2.transcribe import base, chunking, local

_FAKE_MODULE = "tests._fake_transcribe_provider"


class FakeProvider:
    """记录调用次数的假 provider。"""

    name = "fake"

    def __init__(self, segments=None, error=None):
        self.calls = 0
        self._segments = segments if segments is not None else [
            {"start": 0.0, "end": 1.0, "text": "假转写内容。"}
        ]
        self._error = error

    def available(self, cfg):
        return True, ""

    def transcribe(self, audio, *, cfg, on_progress=None):
        self.calls += 1
        if self._error:
            raise self._error
        if on_progress:
            on_progress(1.0, 1.0, "完成")
        return base.Transcript(segments=list(self._segments), extractor="fake:1")


class _FakeRegistered:
    """把假 provider 挂进注册表，退出时恢复现场。"""

    def __init__(self, provider):
        self.provider = provider

    def __enter__(self):
        module = types.ModuleType(_FAKE_MODULE)
        module.PROVIDER = self.provider
        sys.modules[_FAKE_MODULE] = module
        base._PROVIDER_MODULES["fake"] = _FAKE_MODULE
        return self.provider

    def __exit__(self, *exc):
        sys.modules.pop(_FAKE_MODULE, None)
        base._PROVIDER_MODULES.pop("fake", None)
        return False


class TestTranscript(unittest.TestCase):

    def test_text_joins_segments(self):
        transcript = base.Transcript(segments=[
            {"start": 0, "end": 1, "text": "第一句。"},
            {"start": 1, "end": 2, "text": "第二句。"},
        ])
        self.assertEqual(transcript.text, "第一句。\n第二句。")

    def test_roundtrip(self):
        transcript = base.Transcript(
            segments=[{"start": 0.0, "end": 1.0, "text": "内容"}],
            extractor="whisper:mlx:large-v3",
            language="zh",
            duration=12.5,
            warnings=["注意"],
        )
        self.assertEqual(
            base.Transcript.from_dict(transcript.to_dict()).to_dict(),
            transcript.to_dict(),
        )

    def test_from_dict_tolerates_missing_keys(self):
        transcript = base.Transcript.from_dict({})
        self.assertEqual(transcript.segments, [])
        self.assertEqual(transcript.duration, 0.0)


class TestModelResolve(unittest.TestCase):
    """短名要按引擎映射：mlx 认仓库名，faster 认短名。"""

    def test_mlx_short_name_mapped_to_repo(self):
        self.assertEqual(
            local.resolve_model("mlx", "large-v3"),
            "mlx-community/whisper-large-v3-mlx",
        )

    def test_faster_keeps_short_name(self):
        self.assertEqual(local.resolve_model("faster", "large-v3"), "large-v3")

    def test_full_repo_passed_through(self):
        self.assertEqual(
            local.resolve_model("mlx", "mlx-community/whisper-tiny"),
            "mlx-community/whisper-tiny",
        )

    def test_empty_uses_default(self):
        self.assertEqual(local.resolve_model("faster", ""), "large-v3")


class TestProviderSelection(unittest.TestCase):

    def test_unknown_provider_rejected(self):
        cfg = config_mod.Config()
        cfg.transcribe.provider = "nonsense"
        with self.assertRaises(base.TranscribeError) as ctx:
            base.resolve_provider(cfg)
        self.assertIn("未知的 provider", str(ctx.exception))

    def test_forced_cloud_without_credentials_fails_with_hint(self):
        """报错必须同时给出两条路 —— 用户才知道本地也能用。"""
        cfg = config_mod.Config()
        cfg.transcribe.provider = "cloud"
        with self.assertRaises(base.TranscribeError) as ctx:
            base.resolve_provider(cfg)
        message = str(ctx.exception)
        self.assertIn("base_url", message)
        self.assertIn("mlx-whisper", message)

    def test_auto_prefers_local(self):
        """本地可用时不花钱、不联网 —— 路线 A 的核心。"""
        cfg = config_mod.Config()
        cfg.transcribe.provider = "auto"
        ok, _why = base.get_provider("local").available(cfg)
        if not ok:
            self.skipTest("本机未安装任何本地转写引擎")
        provider, warnings = base.resolve_provider(cfg)
        self.assertEqual(provider.name, "local")
        self.assertEqual(warnings, [])


class TestRunWithCache(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.audio = Path(self._td.name) / "audio.wav"
        self.audio.write_bytes(b"fake audio")
        self.addCleanup(self._td.cleanup)

    def _cfg(self, provider="fake") -> config_mod.Config:
        cfg = config_mod.Config()
        cfg.transcribe.provider = provider
        return cfg

    def test_calls_provider_once_then_hits_cache(self):
        provider = FakeProvider()
        with _FakeRegistered(provider):
            first = base.run(self.audio, item_id="item1", cfg=self._cfg())
            second = base.run(self.audio, item_id="item1", cfg=self._cfg())
        self.assertEqual(provider.calls, 1)
        self.assertEqual(first.text, second.text)

    def test_force_bypasses_cache(self):
        provider = FakeProvider()
        with _FakeRegistered(provider):
            base.run(self.audio, item_id="item2", cfg=self._cfg())
            base.run(self.audio, item_id="item2", cfg=self._cfg(), force=True)
        self.assertEqual(provider.calls, 2)

    def test_provider_errors_propagate(self):
        provider = FakeProvider(error=base.TranscribeError("转写炸了"))
        with _FakeRegistered(provider):
            with self.assertRaises(base.TranscribeError):
                base.run(self.audio, item_id="item3", cfg=self._cfg())

    def test_empty_result_is_not_cached(self):
        """空结果不写缓存：可能只是这次音频有问题，下次还该重试。"""
        provider = FakeProvider(segments=[])
        with _FakeRegistered(provider):
            base.run(self.audio, item_id="item4", cfg=self._cfg())
            base.run(self.audio, item_id="item4", cfg=self._cfg())
        self.assertEqual(provider.calls, 2)

    def test_corrupted_cache_is_ignored(self):
        cache = paths.work_path("item5", ".asr.json")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("{ 不是 json", encoding="utf-8")

        provider = FakeProvider()
        with _FakeRegistered(provider):
            base.run(self.audio, item_id="item5", cfg=self._cfg())
        self.assertEqual(provider.calls, 1)

    def test_on_progress_forwarded(self):
        seen: list[tuple] = []
        provider = FakeProvider()
        with _FakeRegistered(provider):
            base.run(
                self.audio, item_id="item6", cfg=self._cfg(),
                on_progress=lambda done, total, note: seen.append((done, total, note)),
            )
        self.assertEqual(seen, [(1.0, 1.0, "完成")])


class TestChunkPlan(unittest.TestCase):
    """切片规划：最后一片不该带重叠尾巴（会多出一秒静音）。"""

    def test_unknown_total_is_single_chunk(self):
        self.assertEqual(chunking.plan(0.0, 600), [(0.0, 0.0)])

    def test_short_audio_single_chunk(self):
        self.assertEqual(chunking.plan(100.0, 600), [(0.0, 100.0)])

    def test_multi_chunk_with_overlap(self):
        self.assertEqual(
            chunking.plan(1500.0, 600, overlap_sec=1.0),
            [(0.0, 601.0), (600.0, 601.0), (1200.0, 300.0)],
        )

    def test_last_chunk_ends_exactly_at_total(self):
        offset, length = chunking.plan(1200.0, 600, overlap_sec=1.0)[-1]
        self.assertAlmostEqual(offset + length, 1200.0)

    def test_step_between_chunks_is_chunk_sec(self):
        chunks = chunking.plan(2000.0, 600, overlap_sec=1.0)
        for earlier, later in zip(chunks, chunks[1:]):
            self.assertAlmostEqual(later[0], earlier[0] + 600)


if __name__ == "__main__":
    unittest.main()
