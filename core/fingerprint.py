"""内容指纹：给一份素材一个稳定身份。

为什么不用文件名 hash
--------------------
整理素材时改名是常态（`xxx_哔哩哔哩_bilibili.mp4` → `未命名.mp4`）。按名字算 id
会把同一个素材当成新条目，产物目录里就会出现两份、还得手动去重。

取值策略
--------
- 单文件：`大小 + 头 1MB + 尾 1MB`。全量哈希 2GB 视频要十几秒，而 L1 经常一次
  扫整个收件目录；头尾采样对「是不是同一个文件」已经足够，且耗时恒定。
- 图集：`文件名 + 大小 + 头 1KB` 的归一化哈希，**不含路径与顺序** ——
  整个目录改名、或压缩包重新打包，只要图片没变就命中同一条。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

FILE_CHUNK = 1 << 20      # 单文件取样块：1 MB
IMAGE_CHUNK = 1 << 10     # 图集取样块：1 KB
ID_LEN = 12


def file_fingerprint(path: str | Path) -> str:
    """单文件内容指纹。读不到内容时抛 OSError，由调用方决定兜底策略。"""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha1(str(size).encode("ascii"))
    with p.open("rb") as f:
        h.update(f.read(FILE_CHUNK))
        if size > FILE_CHUNK:
            f.seek(-FILE_CHUNK, 2)
            h.update(f.read(FILE_CHUNK))
    return h.hexdigest()[:ID_LEN]


def image_set_fingerprint(paths: Iterable[str | Path]) -> str:
    """图集指纹：与路径、顺序无关，只与「这批图片是什么」有关。"""
    entries: list[tuple[str, int, str]] = []
    for raw in paths:
        p = Path(raw)
        try:
            size = p.stat().st_size
            with p.open("rb") as f:
                head = f.read(IMAGE_CHUNK)
        except OSError:
            size, head = -1, b""
        entries.append((p.name, size, hashlib.sha1(head).hexdigest()[:16]))

    h = hashlib.sha1()
    for name, size, head_hash in sorted(entries):
        h.update(f"{name}|{size}|{head_hash}\n".encode("utf-8"))
    return h.hexdigest()[:ID_LEN]


def short_hash(text: str) -> str:
    """退化兜底：读不到任何内容时，至少给个稳定 id，不让流程中断。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:ID_LEN]
