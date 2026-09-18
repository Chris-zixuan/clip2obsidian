"""L2 提取层公共逻辑（视频类平台共享）。

为什么抽在 pipelines/ 顶层而非 core/
-------------------------------------
- core/ 是四层契约与「平台无关」的工具（schema / registry / config / paths /
  textnorm），不该掺入「转写」这种具体的提取流程；
- 字幕解析与 ASR 是**提取层内部实现细节**——下游 distill / publish 根本不关心
  一段文本来自本地字幕还是 ASR，它们只看 clip.json 的 transcript。

暴露给各平台 extract 的主要入口：
- `build_video_transcript(...)`：本地字幕 → ASR 的总调度
- `build_meta(d)`：把 source.json 的精简元信息映射成 schema.Meta
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core import config as config_mod
from core import paths
from core.textnorm import normalize

# 转写相关的用户提示
WARN_ASR = "本文为自动转写，未经人工校对，同音字 / 专有名词可能存在误差"

# SRT / VTT 通用时间轴匹配（HH:MM:SS,mmm 或 HH:MM:SS.mmm）
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_SUB_RE = re.compile(rf"{_TS}\s*-->\s*{_TS}[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)

# 本地字幕文件扩展名（命中则完全跳过 ASR）
_LOCAL_SUB_EXT = (".srt", ".vtt", ".ass")


class ExtractError(Exception):
    """提取失败。消息面向用户，包含可执行的下一步建议。"""


# ------------------------------------------------------------------ 字幕解析
def find_local_subtitle(media_path: Path) -> Path | None:
    """在媒体文件同目录里找本地字幕（.srt / .vtt / .ass）。

    命中本地字幕就完全跳过 ASR——既快又没有同音字错误。
    优先级：① 与媒体**同名前缀**的字幕；② 目录里其它字幕，按 srt > vtt > ass。
    """
    parent = Path(media_path).parent
    if not parent.exists():
        return None
    cands = [f for f in parent.glob("*") if f.suffix.lower() in _LOCAL_SUB_EXT]
    if not cands:
        return None
    stem = Path(media_path).stem
    quality = {".srt": 0, ".vtt": 1, ".ass": 2}
    cands.sort(key=lambda f: (
        0 if f.stem.startswith(stem) else 1,
        quality.get(f.suffix.lower(), 9),
        f.name,
    ))
    return cands[0]


def parse_subtitle(path: Path) -> list[dict]:
    """把字幕文件解析为 [{start, end, text}]，按扩展名分派解析器。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".ass":
        return _parse_ass(path)
    return _parse_srt_vtt(path)


def _parse_srt_vtt(path: Path) -> list[dict]:
    """解析 SRT / VTT。"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[dict] = []
    for m in _SUB_RE.finditer(raw):
        start = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        text = normalize(re.sub(r"\s*\n\s*", " ", (m.group(9) or "").strip()))
        text = re.sub(r"<[^>]+>", "", text).strip()  # 去 VTT 内联标签
        if not text:
            continue
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return segments


def _parse_ass(path: Path) -> list[dict]:
    """解析 ASS：前 9 个逗号分隔 10 个字段，其后都是文本（文本里可能有逗号）。"""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[dict] = []
    in_events = False
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.startswith("[Events]")
            continue
        if not in_events or not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:"):].strip().split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9].strip()
        text = text.replace("\\N", " ").replace("\\n", " ")
        text = re.sub(r"\{[^}]*\}", "", text)  # 去 ASS 样式标签 {\...}
        text = normalize(text)
        if not text:
            continue
        segments.append({
            "start": round(_ass_to_sec(parts[1].strip()), 2),
            "end": round(_ass_to_sec(parts[2].strip()), 2),
            "text": text,
        })
    return segments


def _to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _ass_to_sec(t: str) -> float:
    """ASS 时间码 H:MM:SS.cc。"""
    h, m, rest = t.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


# ------------------------------------------------------------------ 元信息映射
def build_meta(d: dict):
    """把 source.json 的精简元信息映射成 schema.Meta。

    兼容两套字段名：yt-dlp 的 `upload_date` / `uploader` / `duration`，
    以及本地导入时直接塞进来的 `published` / `author` / `duration_sec`。
    缺失一律留空 / 填 0，绝不从外部知识补（避免往知识库灌幻觉）。
    """
    from core import schema

    upload_date = str(d.get("upload_date") or "")
    published = (
        d.get("published")
        or (f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            if len(upload_date) == 8 and upload_date.isdigit() else "")
    )
    return schema.Meta(
        title=(d.get("title") or "").strip(),
        author=(d.get("author") or d.get("uploader") or d.get("channel")
                or d.get("owner") or "").strip(),
        author_id=(d.get("author_id") or d.get("uploader_id") or "").strip(),
        published=published,
        duration_sec=int(d.get("duration") or d.get("duration_sec") or 0),
        stats=schema.Stats(
            like=_int(d.get("like_count") or d.get("like")),
            collect=_int(d.get("collect_count") or d.get("collect")),
            comment=_int(d.get("comment_count") or d.get("comment")),
            share=_int(d.get("repost_count") or d.get("share")),
        ),
        topics=_topics(d),
        description=(d.get("description") or "").strip(),
    )


def _topics(d: dict) -> list[str]:
    """提取平台话题（非知识库受控标签）。"""
    found: list[str] = []
    for t in d.get("tags") or []:
        if isinstance(t, str) and t.strip() and t.strip() not in found:
            found.append(t.strip())
        elif isinstance(t, dict) and (name := (t.get("name") or "").strip()):
            if name not in found:
                found.append(name)
    text = f"{d.get('title') or ''} {d.get('description') or ''}"
    for m in re.findall(r"#([^#\s]+)", text):
        m = m.strip()
        if m and m not in found:
            found.append(m)
    return found[:12]


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ ASR 链路
def _extract_audio(media: Path, wav: Path, cfg: config_mod.Config) -> None:
    """用 ffmpeg 抽 16k 单声道 PCM —— Whisper 的输入规格。"""
    ffmpeg = cfg.tools.ffmpeg_bin
    if not ffmpeg:
        raise ExtractError(
            "找不到 ffmpeg。\n"
            "  安装：brew install ffmpeg\n"
            "  或把 config.toml 的 [tools].ffmpeg 指到可执行文件。"
        )
    media = Path(media)
    if not media.exists():
        raise ExtractError(f"媒体文件不存在：{media}")

    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(media),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExtractError(f"ffmpeg 抽音频失败：\n{(proc.stderr or '')[-800:]}")
    print(f"[extract] 音频已抽取 → {wav.name}", flush=True)


def _run_asr(wav: Path, asr_json: Path, cfg: config_mod.Config) -> None:
    """以子进程调用 ASR，确保使用 [tools].python 指定的依赖环境。"""
    cmd = [
        cfg.tools.python_bin, "-m", "asr",
        "--audio", str(wav),
        "--out", str(asr_json),
        "--backend", cfg.asr.backend,
        "--model", cfg.asr.model,
        "--language", cfg.asr.language,
    ]
    if cfg.asr.initial_prompt:
        cmd += ["--initial-prompt", cfg.asr.initial_prompt]
    if cfg.asr.offline:
        cmd += ["--offline"]

    print(
        f"[extract] 转写中（{cfg.asr.backend}/{cfg.asr.model}），较慢，请耐心…",
        flush=True,
    )
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(paths.PROJECT_ROOT)
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()
        hint = ""
        if cfg.asr.offline:
            hint = (
                "\n  提示：当前 [asr].offline = true，只允许用已缓存的模型。"
                "\n  若刚换了模型，请先在 config.toml 里设为 false 跑一次让模型下载完。"
            )
        raise ExtractError(f"转写失败：\n{tail[-1200:]}{hint}")
    if proc.stdout:
        print(proc.stdout.strip(), flush=True)


def transcribe(clip_id: str, media_path: Path, cfg: config_mod.Config, *, force: bool = False) -> list[dict]:
    """抽音频 → 调 ASR 子进程 → 读回转写结果。两步都有缓存。"""
    media_path = Path(media_path)
    wav = paths.work_path(clip_id, ".wav")
    asr_json = paths.work_path(clip_id, ".asr.json")

    if force or not wav.exists():
        _extract_audio(media_path, wav, cfg)
    if force or not asr_json.exists():
        _run_asr(wav, asr_json, cfg)

    data = json.loads(asr_json.read_text(encoding="utf-8"))
    segments = data.get("segments") or []
    if not segments:
        raise ExtractError(
            f"转写结果为空：{asr_json}\n"
            f"  可能音频为纯音乐或静音。可删掉该文件重试。"
        )
    return segments


# ------------------------------------------------------------------ 总调度
@dataclass
class VideoTranscript:
    """视频内容提取结果。"""

    segments: list[dict] = field(default_factory=list)
    extractor: str = ""          # 如 subtitle:local:srt / asr:faster:medium
    warnings: list[str] = field(default_factory=list)


def build_video_transcript(
    clip_id: str,
    media_path: Path | None,
    cfg: config_mod.Config,
    *,
    force: bool = False,
) -> VideoTranscript:
    """视频内容提取的总调度：本地字幕 → ASR。

    Args:
        clip_id:    clip id（用于缓存 wav / asr.json 的命名）
        media_path: 媒体文件绝对路径（本地导入时为绝对路径）
        cfg:        配置
    """
    warnings: list[str] = []

    # 1) 本地字幕文件（同目录）
    if media_path:
        local = find_local_subtitle(media_path)
        if local:
            segs = parse_subtitle(local)
            if segs:
                return VideoTranscript(
                    segs, f"subtitle:local:{local.suffix.lstrip('.')}", warnings
                )
            warnings.append(f"本地字幕解析为空，回退 ASR：{local}")

    # 2) ASR（兜底，既慢又可能有同音字错误）
    if not media_path:
        raise ExtractError(
            "既没有字幕也没有可用媒体文件，无法转写。\n"
            "  请检查本地文件是否存在。"
        )
    segs = transcribe(clip_id, media_path, cfg, force=force)
    return VideoTranscript(segs, f"asr:{cfg.asr.backend}:{cfg.asr.model}", [WARN_ASR, *warnings])


def now_iso() -> str:
    """当前时间 ISO 8601（带时区），用于 fetched_at。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
