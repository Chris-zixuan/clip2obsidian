"""本地导入层测试（L1）。

核心是 **clip id 的稳定性**。

id 曾按文件名 hash，后果是「视频改名 = 全新条目 = 重复落库」：用户把
`xxx_哔哩哔哩_bilibili.mp4` 整理成 `未命名.mp4`，再跑一次 ingest，同一个视频
就在知识库里出现两遍。现改为只依赖文件内容的指纹，改名、移动目录都不影响。

另一条容易写错的是**平台原生 id（BV 号）**：它适合拼链接、适合人看，但**不能**
拿来当 clip id —— 用户一删文件名里的 BV 后缀，同一个视频又变成新条目。
所以它单独存 `native_id`，只用于链接与展示。
"""

import sys
import tempfile
import unittest
from pathlib import Path

from core import config as config_mod
from core import paths
from pipelines.local import ingest as L


def _cfg() -> config_mod.Config:
    """最小配置：不触网、不依赖本机环境。"""
    cfg = config_mod.Config()
    cfg.ingest.timeout_sec = 5
    # 注意：ToolsConfig 的字段是 `python`，`python_bin` 是只读 property
    cfg.tools.python = sys.executable
    return cfg


class TestStableId(unittest.TestCase):
    """id 只依赖内容，不依赖文件名与路径。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _video(self, name: str = "原片_哔哩哔哩_bilibili.mp4", salt: bytes = b"a", size: int = 4096) -> Path:
        p = self.d / name
        p.write_bytes(salt * size)
        return p

    def test_rename_keeps_id(self):
        """回归：改名曾是「新条目」。"""
        a = self._video()
        first = L._stable_id(a)
        renamed = a.rename(self.d / "整理后的名字_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._stable_id(renamed), first)

    def test_move_to_other_dir_keeps_id(self):
        a = self._video()
        first = L._stable_id(a)
        sub = self.d / "子目录"
        sub.mkdir()
        moved = a.rename(sub / "移动后_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._stable_id(moved), first)

    def test_different_content_different_id(self):
        a = self._video(name="a_哔哩哔哩_bilibili.mp4", salt=b"a")
        b = self._video(name="b_哔哩哔哩_bilibili.mp4", salt=b"b")
        self.assertNotEqual(L._stable_id(a), L._stable_id(b))

    def test_same_size_different_content_different_id(self):
        """只按文件大小算就会撞车。"""
        a = self._video(name="a_哔哩哔哩_bilibili.mp4", salt=b"a", size=4096)
        b = self._video(name="b_哔哩哔哩_bilibili.mp4", salt=b"b", size=4096)
        self.assertNotEqual(L._stable_id(a), L._stable_id(b))

    def test_big_file_samples_tail(self):
        """只采头部会漏掉「前半段相同、后半段不同」的两个视频。

        文件必须大于取样块（1 MB）才会走到尾部采样分支。
        """
        a = self.d / "a_哔哩哔哩_bilibili.mp4"
        a.write_bytes(b"x" * 1_500_000)
        b = self.d / "b_哔哩哔哩_bilibili.mp4"
        b.write_bytes(b"x" * 1_000_000 + b"y" * 500_000)
        self.assertNotEqual(L._stable_id(a), L._stable_id(b))

    def test_big_file_rename_keeps_id(self):
        """大文件走尾部采样分支（seek 负偏移），改名后仍需稳定。"""
        a = self.d / "原名_哔哩哔哩_bilibili.mp4"
        a.write_bytes(b"z" * 3_000_000)
        first = L._stable_id(a)
        renamed = a.rename(self.d / "改名_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._stable_id(renamed), first)

    def test_unreadable_falls_back_to_filename(self):
        """读不到内容（如路径是目录）时退回文件名，不抛异常中断流程。"""
        d = self.d / "是个目录_哔哩哔哩_bilibili.mp4"
        d.mkdir()
        self.assertEqual(L._stable_id(d), L._sha1(d.stem))

    def test_id_length_is_stable(self):
        """id 进目录名与文件名，长度必须恒定。"""
        self.assertEqual(len(L._stable_id(self._video())), 12)


class TestNativeId(unittest.TestCase):
    """BV 号只用于链接与展示，不参与 clip id。"""

    def setUp(self):
        self.spec = __import__("core.registry", fromlist=["get"]).get("bilibili")
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _p(self, name: str) -> Path:
        p = self.d / name
        p.write_bytes(b"x" * 64)
        return p

    def test_from_filename(self):
        p = self._p("认识MAF_BV1xx411c7mD_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._native_id(self.spec, p), "BV1xx411c7mD")

    def test_from_url(self):
        p = self._p("没有任何编号_哔哩哔哩_bilibili.mp4")
        url = "https://www.bilibili.com/video/BV1yy411c7mE?t=10"
        self.assertEqual(L._native_id(self.spec, p, url=url), "BV1yy411c7mE")

    def test_from_sidecar_info_json(self):
        p = self._p("没有任何编号_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._native_id(self.spec, p, {"id": "BV1zz411c7mF"}), "BV1zz411c7mF")

    def test_filename_wins_over_sidecar(self):
        """文件名里的 BV 号更贴近用户手上的这个文件。"""
        p = self._p("A_BV1aa411c7mA_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._native_id(self.spec, p, {"id": "BV1bb411c7mB"}), "BV1aa411c7mA")

    def test_empty_when_nothing_found(self):
        p = self._p("只有平台后缀_哔哩哔哩_bilibili.mp4")
        self.assertEqual(L._native_id(self.spec, p), "")

    def test_synth_url_from_bv(self):
        self.assertEqual(
            L._synth_url("BV1xx411c7mD"), "https://www.bilibili.com/video/BV1xx411c7mD"
        )

    def test_synth_url_empty_without_native_id(self):
        """拿不到就不编造链接。"""
        self.assertEqual(L._synth_url(""), "")


class TestTitleFromFilename(unittest.TestCase):

    def test_strips_bilibili_suffix(self):
        self.assertEqual(
            L._title_from_filename("出门拍了2000张照片_哔哩哔哩_bilibili"),
            "出门拍了2000张照片",
        )

    def test_strips_plain_bilibili(self):
        self.assertEqual(L._title_from_filename("某视频_bilibili"), "某视频")

    def test_keeps_normal_name(self):
        self.assertEqual(L._title_from_filename("普通文件名"), "普通文件名")


class TestIngestEndToEnd(unittest.TestCase):
    """ingest 落盘行为：幂等、改名不重复落库。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _video(self, name: str = "认识MAF_BV1xx411c7mD_哔哩哔哩_bilibili.mp4") -> Path:
        p = self.d / name
        p.write_bytes(b"video-bytes" * 512)
        return p

    def test_writes_source_json_with_absolute_media_path(self):
        source = L.ingest(self._video(), cfg=_cfg())
        self.assertTrue(Path(source["_source_path"]).exists())
        self.assertEqual(source["platform"], "bilibili")
        self.assertEqual(source["native_id"], "BV1xx411c7mD")
        self.assertTrue(Path(source["media"]["path"]).is_absolute())

    def test_media_not_copied_into_raw(self):
        """大视频不复制进 raw/，只记绝对路径。"""
        source = L.ingest(self._video(), cfg=_cfg())
        outdir = Path(source["_source_path"]).parent
        self.assertFalse(
            any(f.suffix == ".mp4" for f in outdir.iterdir()),
            "raw/ 里不应出现媒体副本",
        )

    def test_rerun_hits_cache(self):
        p = self._video()
        L.ingest(p, cfg=_cfg())
        again = L.ingest(p, cfg=_cfg())
        self.assertTrue(again["_cache_hit"])

    def test_force_rewrites(self):
        p = self._video()
        L.ingest(p, cfg=_cfg())
        forced = L.ingest(p, cfg=_cfg(), force=True)
        self.assertFalse(forced["_cache_hit"])

    def test_rename_then_rerun_updates_same_entry(self):
        """回归核心：改名后重跑应更新同一条，而不是新增一条。"""
        p = self._video()
        first = L.ingest(p, cfg=_cfg())
        renamed = p.rename(self.d / "整理后_BV1xx411c7mD_哔哩哔哩_bilibili.mp4")
        second = L.ingest(renamed, cfg=_cfg())

        self.assertEqual(first["platform_id"], second["platform_id"])
        # 用 samefile 比较：macOS 上 /var 是 /private/var 的软链，字符串不相等
        self.assertTrue(Path(second["media"]["path"]).samefile(renamed))
        # 旧路径已失效，缓存算不完整 → 重写；但仍是同一个条目
        self.assertFalse(second["_cache_hit"])
        self.assertEqual(len(list(paths.RAW_DIR.iterdir())), 1)

    def test_two_different_videos_make_two_entries(self):
        L.ingest(self._video("a_BV1aa411c7mA_哔哩哔哩_bilibili.mp4"), cfg=_cfg())
        b = self.d / "b_BV1bb411c7mB_哔哩哔哩_bilibili.mp4"
        b.write_bytes(b"different-content" * 512)
        L.ingest(b, cfg=_cfg())
        self.assertEqual(len(list(paths.RAW_DIR.iterdir())), 2)


class TestScan(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_identifies_bilibili_and_marks_unknown(self):
        (self.d / "视频_哔哩哔哩_bilibili.mp4").write_bytes(b"x")
        (self.d / "说明.txt").write_text("不是视频")
        items = L.scan(self.d)
        labels = {p.name: spec for p, spec, _ in items}
        self.assertIsNotNone(labels["视频_哔哩哔哩_bilibili.mp4"])
        self.assertIsNone(labels["说明.txt"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(L.scan(self.d / "不存在"), [])


if __name__ == "__main__":
    unittest.main()
