"""mlx-whisper 引擎：Apple Silicon 原生加速。

需要额外依赖：`pip install mlx-whisper`
模型从 HuggingFace 拉取，命名形如 `mlx-community/whisper-large-v3-mlx`。
配置里 model 可以只写 `large-v3`，本模块会自动补全为完整 repo 名；
若含 `/` 则视为完整 repo 名。
"""

from __future__ import annotations

from pathlib import Path

from asr.base import AsrEngine, AsrError, AsrResult

DEFAULT_PROMPT = "以下是普通话的口播内容，请使用简体中文输出，并添加标点符号。"

_REPO_TEMPLATE = "mlx-community/whisper-{model}-mlx"


class Engine(AsrEngine):
    name = "mlx"

    def transcribe(
        self,
        audio: Path,
        *,
        model: str = "large-v3",
        language: str = "zh",
        initial_prompt: str = "",
    ) -> AsrResult:
        try:
            import mlx_whisper
        except ImportError as e:
            raise AsrError(
                "当前解释器里没有 mlx-whisper。\n"
                "  安装：pip install mlx-whisper\n"
                "  或把 config.toml 的 [asr].backend 改回 \"faster\"。"
            ) from e

        from core.textnorm import to_simplified

        audio = Path(audio)
        if not audio.exists():
            raise AsrError(f"音频文件不存在：{audio}")

        repo = model if "/" in model else _REPO_TEMPLATE.format(model=model)
        print(f"[asr] mlx-whisper 加载模型 {repo}（首次会自动下载）…", flush=True)
        print(f"[asr] 开始转写 {audio.name} …", flush=True)

        raw = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=repo,
            language=language,
            initial_prompt=initial_prompt or DEFAULT_PROMPT,
            condition_on_previous_text=False,
            verbose=False,
        )

        segments: list[dict] = []
        for seg in raw.get("segments") or []:
            text = to_simplified((seg.get("text") or "").strip())
            if not text:
                continue
            segments.append(
                {
                    "start": round(float(seg.get("start") or 0.0), 2),
                    "end": round(float(seg.get("end") or 0.0), 2),
                    "text": text,
                }
            )

        last_end = segments[-1]["end"] if segments else 0.0
        return AsrResult(
            segments=segments,
            text="\n".join(s["text"] for s in segments),
            language=raw.get("language") or language,
            duration=round(last_end, 2),
            backend=self.name,
            model=repo,
        )
