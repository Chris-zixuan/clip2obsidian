"""ASR 转写引擎子进程入口。

为什么要有这个入口
------------------
依赖（faster-whisper / mlx-whisper）装在独立 venv 里，而主程序可能由别的
解释器启动。统一以子进程 + `[tools].python` 指定的解释器运行，可保证用对
依赖环境，同时让主程序保持「只用标准库」。

用法：
    python -m asr --audio work/x.wav --out work/x.asr.json --backend faster --model medium
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许以 `python -m asr` 形式在任何 cwd 下运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="clip2obsidian · ASR 转写子进程")
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="转写结果 JSON 落盘路径")
    ap.add_argument("--backend", default="faster")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--initial-prompt", default="")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="强制离线，只使用已缓存的模型（HF_HUB_OFFLINE=1）",
    )
    args = ap.parse_args(argv)

    if args.offline:
        # 必须在引擎惰性导入 (huggingface_hub) 之前设置
        os.environ["HF_HUB_OFFLINE"] = "1"

    from asr.base import AsrError, get_engine

    try:
        engine = get_engine(args.backend)
        result = engine.transcribe(
            args.audio,
            model=args.model,
            language=args.language,
            initial_prompt=args.initial_prompt,
        )
    except AsrError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[ok] {len(result.segments)} 段 / {result.duration:.1f}s → {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
