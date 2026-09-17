"""faster-whisper 引擎：CPU 可跑，不依赖 Apple Silicon。

这是当前默认引擎（迁移自旧项目的 transcribe.py）。
速度参考：medium + CPU int8，约 1:0.65 实时率（8.5 分钟音频 ≈ 5.5 分钟）。
若机器是 Apple Silicon，改用 `mlx` 后端会快数倍。
"""

from __future__ import annotations

from pathlib import Path

from asr.base import AsrEngine, AsrError, AsrResult

DEFAULT_PROMPT = "以下是普通话的口播内容，请使用简体中文输出，并添加标点符号。"


class Engine(AsrEngine):
    name = "faster"

    def transcribe(
        self,
        audio: Path,
        *,
        model: str = "medium",
        language: str = "zh",
        initial_prompt: str = "",
    ) -> AsrResult:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise AsrError(
                "当前解释器里没有 faster-whisper。\n"
                "  请确认 config.toml 的 [tools].python 指向装好依赖的解释器，"
                "或改用 backend = \"mlx\"。"
            ) from e

        from core.textnorm import to_simplified

        audio = Path(audio)
        if not audio.exists():
            raise AsrError(f"音频文件不存在：{audio}")

        print(f"[asr] faster-whisper 加载模型 {model}（首次会自动下载）…", flush=True)
        whisper = WhisperModel(model, device="cpu", compute_type="int8")

        print(f"[asr] 开始转写 {audio.name} …", flush=True)
        iterator, info = whisper.transcribe(
            str(audio),
            language=language,
            beam_size=5,
            vad_filter=True,
            initial_prompt=initial_prompt or DEFAULT_PROMPT,
            condition_on_previous_text=False,
        )

        segments: list[dict] = []
        for seg in iterator:
            # Whisper 中文长音频后段易输出繁体，统一转简体
            text = to_simplified((seg.text or "").strip())
            if not text:
                continue
            segments.append(
                {
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": text,
                }
            )
            print(f"  [{_fmt(seg.start)}] {text}", flush=True)

        return AsrResult(
            segments=segments,
            text="\n".join(s["text"] for s in segments),
            language=getattr(info, "language", language) or language,
            duration=round(float(getattr(info, "duration", 0.0) or 0.0), 2),
            backend=self.name,
            model=model,
        )


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec or 0), 60)
    return f"{m:02d}:{s:02d}"
