"""抖音提取层（L2）：原始物料 → clip.json。

这一层只做转换，不做判断：字幕或 ASR → 带时间轴的结构化文本 + 元信息。
「哪些是重点」属于 L3 蒸馏层的事。

字幕优先
--------
平台/yt-dlp 能取到字幕时直接解析，既省掉整段转写时间，又没有同音字错误。
只有取不到字幕才抽音频跑 ASR。
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from core import config as config_mod
from core import paths, schema
from core.textnorm import normalize

PLATFORM = "douyin"

# SRT / VTT 通用时间轴匹配
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_SUB_RE = re.compile(rf"{_TS}\s*-->\s*{_TS}[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)

WARN_ASR = "本文为自动转写，未经人工校对，同音字 / 专有名词可能存在误差"
WARN_AUTO_SUB = "字幕由平台自动生成，可能存在识别误差"


class ExtractError(Exception):
    """提取失败。消息面向用户。"""


# ------------------------------------------------------------------ 主流程
def extract(source_path: Path, *, cfg: config_mod.Config | None = None, force: bool = False) -> schema.Clip:
    """读 L1 的 source.json，产出 clip.json。"""
    cfg = cfg or config_mod.get()
    source_path = Path(source_path)
    if not source_path.exists():
        raise ExtractError(f"找不到采集结果：{source_path}\n  请先跑 L1（fetch）。")

    source = json.loads(source_path.read_text(encoding="utf-8"))
    meta_dump = _load_meta(source)

    clip_id = f"{PLATFORM}:{source['platform_id']}"
    clip = schema.new_clip(
        platform=PLATFORM,
        platform_id=source["platform_id"],
        content_type="video",
        source_url=source["source_url"],
        canonical_url=source.get("canonical_url") or source["source_url"],
        fetched_at=source.get("fetched_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    clip.meta = _build_meta(meta_dump)
    clip.provenance.fetcher = source.get("fetcher") or ""
    clip.provenance.warnings.extend(source.get("warnings") or [])

    media = source.get("media") or {}
    if media.get("path"):
        clip.assets.append(
            schema.Asset(kind="video", path=media["path"], order=0, role="content")
        )

    # 内容主体：字幕优先 → ASR
    subtitle = source.get("subtitle") or {}
    if subtitle.get("path"):
        segments = _parse_subtitle(paths.PROJECT_ROOT / subtitle["path"])
        kind = subtitle.get("kind") or "unknown"
        lang = subtitle.get("lang") or "?"
        clip.provenance.extractor = f"subtitle:{kind}:{lang}"
        if kind == "auto":
            clip.provenance.warnings.append(WARN_AUTO_SUB)
        if not segments:
            raise ExtractError(
                f"字幕文件解析后为空：{subtitle['path']}\n"
                f"  可删掉该字幕文件后重跑，让它回退到 ASR 转写。"
            )
    else:
        segments = _transcribe(clip_id, media, cfg, force=force)
        clip.provenance.extractor = f"asr:{cfg.asr.backend}:{cfg.asr.model}"
        clip.provenance.warnings.append(WARN_ASR)

    clip.content.transcript = [
        schema.Segment(start=s["start"], end=s["end"], text=s["text"]) for s in segments
    ]

    clip.save(paths.work_path(clip_id, ".clip.json"))
    return clip


# ------------------------------------------------------------------ 元信息
def _load_meta(source: dict) -> dict:
    meta_path = source.get("meta_path")
    if not meta_path:
        return {}
    p = paths.PROJECT_ROOT / meta_path
    if not p.exists():
        raise ExtractError(f"元信息文件不存在：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_meta(d: dict) -> schema.Meta:
    upload_date = str(d.get("upload_date") or "")
    published = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        if len(upload_date) == 8 and upload_date.isdigit()
        else ""
    )
    return schema.Meta(
        title=(d.get("title") or "").strip(),
        author=(d.get("channel") or d.get("uploader") or "").strip(),
        author_id=(d.get("uploader_id") or "").strip(),
        published=published,
        duration_sec=int(d.get("duration") or 0),
        stats=schema.Stats(
            like=_int(d.get("like_count")),
            collect=_int(d.get("collect_count")),
            comment=_int(d.get("comment_count")),
            share=_int(d.get("repost_count")),
        ),
        topics=_topics(d),
        description=(d.get("description") or "").strip(),
    )


def _topics(d: dict) -> list[str]:
    """提取平台话题。注意：这是平台话题，不是知识库受控标签。"""
    found: list[str] = []
    for t in d.get("tags") or []:
        if isinstance(t, str) and t.strip() and t.strip() not in found:
            found.append(t.strip())
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


# ------------------------------------------------------------------ 字幕解析
def _parse_subtitle(path: Path) -> list[dict]:
    """解析 SRT / VTT 为 [{start, end, text}]。"""
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


def _to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


# ------------------------------------------------------------------ ASR 链路
def _transcribe(clip_id: str, media: dict, cfg: config_mod.Config, *, force: bool = False) -> list[dict]:
    """抽音频 → 调 ASR 子进程 → 读回转写结果。两步都有缓存。"""
    if not media.get("path"):
        raise ExtractError(
            "既没有字幕也没有可用的媒体文件，无法转写。\n"
            "  请用 --force 重跑采集，或检查 config 的 [fetch].prefer_subtitle。"
        )

    media_path = paths.PROJECT_ROOT / media["path"]
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


def _extract_audio(media: Path, wav: Path, cfg: config_mod.Config) -> None:
    """用 ffmpeg 抽 16k 单声道 PCM —— Whisper 的输入规格。"""
    ffmpeg = cfg.tools.ffmpeg_bin
    if not ffmpeg:
        raise ExtractError(
            "找不到 ffmpeg。\n"
            "  安装：brew install ffmpeg\n"
            "  或把 config.toml 的 [tools].ffmpeg 指到可执行文件。"
        )
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
        raise ExtractError(
            f"ffmpeg 抽音频失败：\n{(proc.stderr or '')[-800:]}"
        )
    print(f"[extract] 音频已抽取 → {wav.name}", flush=True)


def _run_asr(wav: Path, asr_json: Path, cfg: config_mod.Config) -> None:
    """以子进程调用 ASR，确保使用 [tools].python 指定的依赖环境。"""
    if cfg.asr.backend == "none":
        raise ExtractError(
            "[asr].backend = none，但本次未命中平台字幕，无法产出内容。\n"
            "  请把 backend 改为 faster 或 mlx。"
        )

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
        f"[extract] 转写中（{cfg.asr.backend}/{cfg.asr.model}），"
        f"较慢，请耐心…",
        flush=True,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(paths.PROJECT_ROOT))
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
