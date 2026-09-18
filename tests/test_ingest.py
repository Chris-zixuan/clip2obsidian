"""L1 登记层测试。

三个重点：

1. **id 稳定性** —— 曾按文件名 hash，后果是「素材改名 = 新条目 = 重复处理」。
2. **zip 安全** —— 压缩包内容不可信：目录穿越会覆盖项目外文件，解压炸弹会塞满磁盘。
3. **幂等** —— 重复登记不该产生第二条，也不该重复解压。
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core import config as config_mod
from core import paths
from l1 import ingest, ziputil


def _cfg() -> config_mod.Config:
    cfg = config_mod.Config()
    cfg.ingest.timeout_sec = 5
    cfg.ingest.max_unzip_mb = 4
    return cfg


class TestStableId(unittest.TestCase):
    """id 只依赖内容，不依赖文件名与路径。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _video(self, name="原片_BV1xx411c7mD_哔哩哔哩_bilibili.mp4", salt=b"a", size=4096) -> Path:
        path = self.dir / name
        path.write_bytes(salt * size)
        return path

    def test_rename_keeps_id(self):
        path = self._video()
        first = ingest.register(path, cfg=_cfg())[0]["id"]
        renamed = path.rename(self.dir / "整理后_BV1xx411c7mD_哔哩哔哩_bilibili.mp4")
        second = ingest.register(renamed, cfg=_cfg())[0]
        self.assertEqual(first, second["id"])
        self.assertFalse(second["_cache_hit"])   # 旧路径失效 → 重写，但仍是同一条
        self.assertEqual(len(list(paths.RAW_DIR.glob("*/job.json"))), 1)

    def test_big_file_rename_keeps_id(self):
        """走尾部采样分支的大文件也要稳定。"""
        path = self.dir / "大文件_BV1xx411c7mD_哔哩哔哩_bilibili.mp4"
        path.write_bytes(b"z" * 3_000_000)
        first = ingest.register(path, cfg=_cfg())[0]["id"]
        renamed = path.rename(self.dir / "改名_BV1xx411c7mD_哔哩哔哩_bilibili.mp4")
        self.assertEqual(ingest.register(renamed, cfg=_cfg())[0]["id"], first)

    def test_different_content_different_id(self):
        a = self._video(name="a_BV1aa411c7mA_哔哩哔哩_bilibili.mp4", salt=b"a")
        b = self._video(name="b_BV1bb411c7mB_哔哩哔哩_bilibili.mp4", salt=b"b")
        self.assertNotEqual(
            ingest.register(a, cfg=_cfg())[0]["id"],
            ingest.register(b, cfg=_cfg())[0]["id"],
        )

    def test_image_dir_rename_keeps_id(self):
        """图集指纹与路径无关：整目录改名仍命中同一条。"""
        first_dir = self.dir / "相册A"
        first_dir.mkdir()
        (first_dir / "1.jpg").write_bytes(b"one")
        (first_dir / "2.jpg").write_bytes(b"two")
        first = ingest.register(first_dir, cfg=_cfg())[0]["id"]

        second_dir = self.dir / "相册B"
        first_dir.rename(second_dir)
        self.assertEqual(ingest.register(second_dir, cfg=_cfg())[0]["id"], first)


class TestJobShape(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _video(self, name="视频_BV1xx411c7mD_哔哩哔哩_bilibili.mp4") -> Path:
        path = self.dir / name
        path.write_bytes(b"video" * 256)
        return path

    def test_job_fields_and_file(self):
        job = ingest.register(self._video(), cfg=_cfg())[0]
        self.assertEqual(job["type"], "video")
        self.assertEqual(job["platform"], "bilibili")
        self.assertEqual(job["native_id"], "BV1xx411c7mD")
        self.assertEqual(job["meta"]["title"], "视频")
        self.assertTrue(job["meta"]["source_url"].endswith("BV1xx411c7mD"))
        self.assertTrue(Path(job["_job_path"]).exists())

    def test_media_not_copied_into_raw(self):
        job = ingest.register(self._video(), cfg=_cfg())[0]
        out_dir = Path(job["_job_path"]).parent
        self.assertFalse(any(p.suffix == ".mp4" for p in out_dir.iterdir()))

    def test_second_register_hits_cache(self):
        path = self._video()
        ingest.register(path, cfg=_cfg())
        self.assertTrue(ingest.register(path, cfg=_cfg())[0]["_cache_hit"])

    def test_force_reregisters(self):
        path = self._video()
        ingest.register(path, cfg=_cfg())
        self.assertFalse(ingest.register(path, cfg=_cfg(), force=True)[0]["_cache_hit"])

    def test_image_job_lists_images(self):
        images = self.dir / "相册"
        images.mkdir()
        (images / "1.jpg").write_bytes(b"one")
        (images / "2.jpg").write_bytes(b"two")
        job = ingest.register(images, cfg=_cfg())[0]
        self.assertEqual(job["type"], "image_set")
        self.assertEqual(len(job["images"]), 2)
        self.assertEqual(job["material"], "")

    def test_load_jobs(self):
        ingest.register(self._video(), cfg=_cfg())
        jobs = ingest.load_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["id"])


class TestArchiveHandling(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _zip_of_images(self, name="相册.zip") -> Path:
        images = self.dir / "src"
        images.mkdir(exist_ok=True)
        (images / "1.jpg").write_bytes(b"one")
        (images / "2.jpg").write_bytes(b"two")
        archive = self.dir / name
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(images / "1.jpg", "1.jpg")
            zf.write(images / "2.jpg", "2.jpg")
        return images, archive

    def test_zip_matches_directory_id(self):
        """回归核心：同一个素材，给目录和给 zip 必须是同一条。"""
        images, archive = self._zip_of_images()
        from_dir = ingest.register(images, cfg=_cfg())[0]["id"]
        from_zip = ingest.register(archive, cfg=_cfg())[0]
        self.assertEqual(from_zip["id"], from_dir)
        self.assertEqual(from_zip["type"], "image_set")

    def test_zip_is_extracted_under_raw(self):
        _images, archive = self._zip_of_images()
        ingest.register(archive, cfg=_cfg())
        self.assertTrue((paths.RAW_DIR / ".unzip").is_dir())

    def test_rejects_zip_slip(self):
        archive = self.dir / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo("../evil.txt")
            zf.writestr(info, "x")
        with self.assertRaises(ziputil.ArchiveError) as ctx:
            ziputil.extract_zip(archive, self.dir / "out")
        self.assertIn("越权路径", str(ctx.exception))

    def test_rejects_oversized_content(self):
        archive = self.dir / "big.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("a.bin", b"x" * 2048)
        with self.assertRaises(ziputil.ArchiveError):
            ziputil.extract_zip(archive, self.dir / "out", max_total_mb=0)

    def test_rejects_too_many_members(self):
        archive = self.dir / "many.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for i in range(4):
                zf.writestr(f"{i}.txt", "x")
        original = ziputil.MAX_MEMBERS
        ziputil.MAX_MEMBERS = 3
        try:
            with self.assertRaises(ziputil.ArchiveError):
                ziputil.extract_zip(archive, self.dir / "out")
        finally:
            ziputil.MAX_MEMBERS = original

    def test_broken_zip_reports_friendly_error(self):
        archive = self.dir / "broken.zip"
        archive.write_bytes(b"not a zip at all")
        with self.assertRaises(ziputil.ArchiveError) as ctx:
            ziputil.extract_zip(archive, self.dir / "out")
        self.assertIn("损坏", str(ctx.exception))


class TestScan(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_lists_known_and_unknown(self):
        (self.dir / "视频_BV1xx411c7mD_哔哩哔哩_bilibili.mp4").write_bytes(b"x")
        (self.dir / "说明.txt").write_text("无关")
        items = {p.name: kind for p, kind, _ in ingest.scan(self.dir)}
        self.assertEqual(items["视频_BV1xx411c7mD_哔哩哔哩_bilibili.mp4"], "video")
        self.assertIsNone(items["说明.txt"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(ingest.scan(self.dir / "不存在"), [])

    def test_image_dir_reported_as_single(self):
        images = self.dir / "相册"
        images.mkdir()
        (images / "1.jpg").write_bytes(b"one")
        items = ingest.scan(self.dir)
        self.assertEqual(items[0][1], "image_set")


if __name__ == "__main__":
    unittest.main()
