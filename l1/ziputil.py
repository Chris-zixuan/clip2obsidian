"""zip 安全解压：防目录穿越、防解压炸弹。

压缩包是用户从各处下载来的，内容不可信。两道防线都必须有：

- **目录穿越（zip slip）**：成员名写成 `../../.ssh/authorized_keys` 或绝对路径时，
  天真的 `extractall` 会覆盖项目外的文件。
- **解压炸弹**：一个几十 KB 的包可以声明解压出几百 GB（`file_size` 与实际写入量
  都不可信），把磁盘塞满。

所以先全量校验成员，再解压。
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

# 成员数量上限：正常素材包远达不到，防止「一堆空文件」式的攻击
MAX_MEMBERS = 5000


class ArchiveError(Exception):
    """压缩包不可用。消息面向用户。"""


def extract_zip(archive: str | Path, dest: str | Path, *, max_total_mb: int = 4096) -> Path:
    """把 zip 解压到 dest（已存在则复用）。

    Args:
        max_total_mb: 解压后内容总量上限，超出即中止
    """
    archive, dest = Path(archive), Path(dest)
    if not archive.is_file():
        raise ArchiveError(f"压缩包不存在：{archive}")

    try:
        with zipfile.ZipFile(archive) as zf:
            _validate(zf, dest, max_total_mb)
            dest.mkdir(parents=True, exist_ok=True)
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"压缩包已损坏或不是 zip：{archive}\n  {e}") from e
    return dest


def _validate(zf: zipfile.ZipFile, dest: Path, max_total_mb: int) -> None:
    members = zf.infolist()
    if len(members) > MAX_MEMBERS:
        raise ArchiveError(
            f"压缩包成员过多（{len(members)} 个，上限 {MAX_MEMBERS}）：{zf.filename}"
        )

    root = dest.resolve()
    total = 0
    for info in members:
        target = (root / info.filename).resolve()
        if target != root and root not in target.parents:
            raise ArchiveError(
                f"压缩包里有越权路径，已中止解压：{info.filename}\n"
                f"  解压这类压缩包可能覆盖项目外的文件。"
            )
        total += max(0, info.file_size)
        if total > max_total_mb * 1024 * 1024:
            raise ArchiveError(
                f"解压后内容超过上限（{max_total_mb} MB），已中止。\n"
                f"  确认压缩包可信后，可调大 config.toml 的 [ingest].max_unzip_mb。"
            )


def unzip_root(fingerprint: str) -> Path:
    """解压目录的落点：`raw/.unzip/{指纹}/`。

    用压缩包指纹命名，同一个包重复解压会复用同一目录，不会越解越多。
    """
    from core import paths

    return paths.RAW_DIR / ".unzip" / fingerprint


def is_inside(path: Path, parent: Path) -> bool:
    """path 是否位于 parent 之内（软链也按真实路径判断）。"""
    try:
        return os.path.commonpath([str(Path(path).resolve()), str(Path(parent).resolve())]) == str(
            Path(parent).resolve()
        )
    except (ValueError, OSError):
        return False
