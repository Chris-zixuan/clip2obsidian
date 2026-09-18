"""音频切片：为「确定的进度」与「可控的内存」服务。

为什么必须切片
--------------
`mlx-whisper` 的 `transcribe()` 是滑窗循环结束后一次性返回结果，**没有任何细粒度
回调**；解析它内部的 tqdm 输出又太脆弱（版本一升级就断）。切成固定时长后逐片调用，
每片完成就是一格确定进度，同时也顺带得到两个好处：超长音频不会一口吃满内存、
单片失败可以只重试那一片。

重叠 1 秒
---------
片边界可能正好落在词中间。让相邻片重叠 1 秒，并丢弃落在重叠区的片段（留给下一片），
这样被切开的词至少会被其中一片完整识别到。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import config as config_mod

OVERLAP_SEC = 1.0

# 音频本身不长的下限：不足一片时没必要切片
MIN_CHUNK_SEC = 1.0


@dataclass(frozen=True)
class Chunk:
    """一个切片。`offset` 是它在整段音频里的起始秒。"""

    index: int
    path: Path
    offset: float
    length: float


def plan(total: float, chunk_sec: int, overlap_sec: float = OVERLAP_SEC) -> list[tuple[float, float]]:
    """规划切片：返回 [(起始秒, 长度秒)]。

    纯函数，便于测试。总长未知（<=0）时返回单片 —— 宁可没有进度，也不要猜错边界。
    """
    if total <= 0:
        return [(0.0, 0.0)]
    if chunk_sec <= 0 or total <= chunk_sec:
        return [(0.0, total)]

    chunks: list[tuple[float, float]] = []
    offset = 0.0
    while offset < total - MIN_CHUNK_SEC:
        length = min(chunk_sec + overlap_sec, total - offset)
        chunks.append((offset, length))
        if length < chunk_sec + overlap_sec:
            break   # 已经是最后一段
        offset += chunk_sec
    return chunks or [(0.0, total)]


def split(
    wav: Path,
    out_dir: Path,
    *,
    cfg: config_mod.Config,
    total: float,
    chunk_sec: int,
    overlap_sec: float = OVERLAP_SEC,
) -> list[Chunk]:
    """按 plan() 把音频切成若干 wav 文件。

    用 ffmpeg 的 `-ss/-t` + `-c copy`：PCM 数据无需重编码，切片是毫秒级操作。
    """
    ffmpeg = cfg.tools.ffmpeg_bin
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，无法切片")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[Chunk] = []
    for index, (offset, length) in enumerate(plan(total, chunk_sec, overlap_sec)):
        dst = out_dir / f"chunk_{index:04d}.wav"
        if not dst.exists() or dst.stat().st_size == 0:
            cmd = [
                ffmpeg, "-y", "-nostdin", "-loglevel", "error",
                "-ss", f"{offset:.3f}", "-t", f"{length:.3f}",
                "-i", str(wav), "-c", "copy", str(dst),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not dst.exists():
                raise RuntimeError(
                    f"切片失败（第 {index} 片）：\n{(proc.stderr or '')[-500:]}"
                )
        chunks.append(Chunk(index=index, path=dst, offset=offset, length=length))
    return chunks
