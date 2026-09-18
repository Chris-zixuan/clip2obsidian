"""媒体工具：ffprobe 取时长、ffmpeg 抽音轨（带进度上报）。

抽音轨是转写的前置步骤，也是整条链路里第一个耗时操作 —— 所以它必须能报进度：
`ffmpeg -progress pipe:1` 会持续输出已处理时间，配合 ffprobe 得到的总时长就能给出
确定百分比。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core import config as config_mod
from core.progress import NullReporter, ProgressReporter, format_duration, parse_ffmpeg_progress


class MediaError(Exception):
    """媒体处理失败。消息面向用户、带可执行的下一步建议。"""


def probe_duration(media: str | Path, cfg: config_mod.Config) -> float:
    """用 ffprobe 取时长（秒）。取不到返回 0 —— 退化成不确定进度，不阻断流程。"""
    ffprobe = cfg.tools.ffprobe_bin
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(media),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def extract_audio(
    media: str | Path,
    wav: str | Path,
    cfg: config_mod.Config,
    *,
    reporter: ProgressReporter | None = None,
) -> Path:
    """把视频 / 音频转成 16k 单声道 PCM WAV（whisper 的输入规格），带进度。

    音频文件也走这里：统一转成同一规格，后面的分片与转写就不必区分输入形态。
    """
    reporter = reporter or NullReporter()
    ffmpeg = cfg.tools.ffmpeg_bin
    if not ffmpeg:
        raise MediaError(
            "找不到 ffmpeg。\n"
            "  安装：brew install ffmpeg\n"
            "  或把 config.toml 的 [tools].ffmpeg 指到可执行文件。"
        )

    media, wav = Path(media), Path(wav)
    if not media.exists():
        raise MediaError(f"媒体文件不存在：{media}")
    wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg, "-y", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1",
        "-i", str(media),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav),
    ]

    total = probe_duration(media, cfg)
    reporter.start(total, f"抽取音轨 {media.name}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except OSError as e:
        raise MediaError(f"无法启动 ffmpeg：{e}") from e

    assert proc.stdout is not None
    for line in proc.stdout:
        done = parse_ffmpeg_progress(line)
        if done is not None:
            reporter.update(min(done, total) if total else done)

    stderr = proc.stderr.read() if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0:
        reporter.close("抽取音轨失败")
        raise MediaError(f"ffmpeg 抽取音频失败：\n{stderr[-800:]}")

    if not wav.exists() or wav.stat().st_size == 0:
        reporter.close("抽取音轨失败")
        raise MediaError(f"ffmpeg 没有产出音频文件：{wav}")

    reporter.close(f"音轨已抽取（{format_duration(total)}）→ {wav.name}")
    return wav
