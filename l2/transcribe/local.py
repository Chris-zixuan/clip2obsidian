"""本地转写：mlx-whisper（Apple Silicon GPU）与 faster-whisper（CPU）。

引擎选择
--------
`engine = auto` 时优先 mlx —— 同一台机器上实测 884 秒视频约 230 秒，比 CPU 的
faster-whisper（约 600 秒）快一倍多。两者输出被归一成同一结构，上层无感。

两个必须知道的引擎差异
--------------------
1. **mlx 版不支持 `beam_size`**：传了会直接抛 `NotImplementedError`，所以两条
   分支的参数并不对称，这是有意为之。
2. **`HF_HUB_OFFLINE` 必须在引擎导入前设置**，否则 huggingface_hub 已经联网
   检查过了，离线开关形同虚设。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from core import config as config_mod
from core import paths
from core.textnorm import to_simplified
from l2.media import probe_duration
from l2.transcribe import chunking
from l2.transcribe.base import ProgressCb, TranscribeError, Transcript, WARN_ASR

DEFAULT_PROMPT = "以下是普通话的口播内容，请使用简体中文输出，并添加标点符号。"

# faster-whisper 加载模型很慢（medium 几十秒），必须跨片、跨次调用复用
_faster_models: dict[str, object] = {}


class LocalProvider:
    """本地引擎，内部自动在 mlx / faster 之间选择。"""

    name = "local"

    def available(self, cfg: config_mod.Config) -> tuple[bool, str]:
        _apply_offline_env(cfg)
        if _pick_engine(cfg) is None:
            return False, "未安装 mlx-whisper 或 faster-whisper"
        return True, ""

    def transcribe(
        self,
        audio: Path,
        *,
        cfg: config_mod.Config,
        on_progress: ProgressCb | None = None,
    ) -> Transcript:
        _apply_offline_env(cfg)
        engine = _pick_engine(cfg)
        if engine is None:
            raise TranscribeError(
                "本地没有可用的转写引擎。\n"
                "  Apple Silicon：pip install mlx-whisper\n"
                "  其它平台：pip install faster-whisper"
            )

        audio = Path(audio)
        model = resolve_model(engine, cfg.transcribe.model)
        total = probe_duration(audio, cfg)

        # 切片落 work/{id}/chunks/：audio 是 work/{id}.wav，stem 就是条目 id
        chunk_dir = paths.work_dir(audio.stem) / "chunks"
        chunks = chunking.split(
            audio, chunk_dir,
            cfg=cfg, total=total, chunk_sec=cfg.transcribe.chunk_sec,
        )

        segments: list[dict] = []
        language = ""
        count = len(chunks)
        for chunk in chunks:
            piece, piece_lang = _run_engine(engine, model, chunk.path, cfg)
            language = language or piece_lang
            is_last = chunk.index == count - 1
            for seg in piece:
                start = chunk.offset + float(seg["start"])
                # 落在重叠区的片段留给下一片，避免同一句话出现两次
                if not is_last and start >= chunk.offset + cfg.transcribe.chunk_sec:
                    continue
                segments.append({
                    "start": round(start, 2),
                    "end": round(chunk.offset + float(seg["end"]), 2),
                    "text": seg["text"],
                })
            if on_progress:
                done = min(chunk.offset + chunk.length, total) if total else chunk.offset + chunk.length
                on_progress(done, total, f"第 {chunk.index + 1}/{count} 片")

        if not segments:
            raise TranscribeError(
                "转写结果为空。\n"
                "  可能音频是纯音乐或静音；若确认有语音，可删掉 work/ 下的 .asr.json 重试。"
            )

        return Transcript(
            segments=segments,
            extractor=f"whisper:{engine}:{model}",
            language=language or cfg.transcribe.language,
            duration=total,
            warnings=[WARN_ASR],
        )


# ------------------------------------------------------------------ 引擎分派
def _run_engine(engine: str, model: str, wav: Path, cfg: config_mod.Config) -> tuple[list[dict], str]:
    prompt = cfg.transcribe.initial_prompt or DEFAULT_PROMPT
    if engine == "mlx":
        return _run_mlx(model, wav, cfg, prompt)
    return _run_faster(model, wav, cfg, prompt)


def _run_mlx(model: str, wav: Path, cfg: config_mod.Config, prompt: str) -> tuple[list[dict], str]:
    import mlx_whisper   # 惰性导入：只有真要用 mlx 才要求装它

    out = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=model,
        language=cfg.transcribe.language,      # 经 decode_options 传下去
        initial_prompt=prompt,
        condition_on_previous_text=False,
        verbose=None,
        # 注意：mlx 版没有 beam_size（传了会抛 NotImplementedError）
    )
    return _normalize(out.get("segments") or []), str(out.get("language") or "")


def _run_faster(model: str, wav: Path, cfg: config_mod.Config, prompt: str) -> tuple[list[dict], str]:
    from faster_whisper import WhisperModel

    whisper = _faster_models.get(model)
    if whisper is None:
        whisper = WhisperModel(model, device="cpu", compute_type="int8")
        _faster_models[model] = whisper

    iterator, info = whisper.transcribe(
        str(wav),
        language=cfg.transcribe.language,
        beam_size=5,
        vad_filter=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
    raw = [{"start": float(s.start), "end": float(s.end), "text": s.text or ""} for s in iterator]
    return _normalize(raw), str(getattr(info, "language", "") or "")


def _normalize(segments: list) -> list[dict]:
    """统一成 [{start, end, text}]，顺带做繁转简与去空。"""
    out: list[dict] = []
    for seg in segments:
        if isinstance(seg, dict):
            start, end, text = seg.get("start", 0.0), seg.get("end", 0.0), seg.get("text", "")
        else:
            start, end, text = getattr(seg, "start", 0.0), getattr(seg, "end", 0.0), getattr(seg, "text", "")
        text = to_simplified(str(text or "").strip())
        if not text:
            continue
        out.append({"start": float(start), "end": float(end), "text": text})
    return out


# ------------------------------------------------------------------ 选择与命名
def _pick_engine(cfg: config_mod.Config) -> str | None:
    want = (cfg.transcribe.engine or "auto").strip().lower()
    if want == "mlx":
        return "mlx" if _has_module("mlx_whisper") else None
    if want == "faster":
        return "faster" if _has_module("faster_whisper") else None

    # auto：Apple Silicon 上 mlx 明显更快，优先；其余情况用 CPU 版
    if paths.IS_MACOS and _has_module("mlx_whisper"):
        return "mlx"
    if _has_module("faster_whisper"):
        return "faster"
    if _has_module("mlx_whisper"):
        return "mlx"
    return None


def _has_module(name: str) -> bool:
    """只探测是否安装，不真的导入 —— 导入 mlx 会初始化 Metal，代价不小。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def resolve_model(engine: str, model: str) -> str:
    """短模型名 → 引擎认识的标识。

    带 `/` 的视为完整仓库名直接透传，便于指定量化版或自定义仓库。
    """
    model = (model or "large-v3").strip()
    if "/" in model:
        return model
    if engine == "mlx":
        return f"mlx-community/whisper-{model}-mlx"
    return model


def _apply_offline_env(cfg: config_mod.Config) -> None:
    """强制离线：必须在 huggingface_hub 导入之前设置环境变量。"""
    if cfg.transcribe.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"


def describe(cfg: config_mod.Config) -> str:
    """给自检命令用的一句话说明。"""
    engine = _pick_engine(cfg)
    if engine is None:
        return "未安装（pip install mlx-whisper 或 faster-whisper）"
    return f"{engine} / {resolve_model(engine, cfg.transcribe.model)}"


PROVIDER = LocalProvider()
