"""转写 provider 层。

- `base.py`：统一结果结构 `Transcript`、provider 协议、选择与降级、结果缓存
- `chunking.py`：音频切片（为了确定进度、限制内存、单片重试）
- `local.py`：本地实现，mlx-whisper 与 faster-whisper 双引擎
- `openai_compat.py`：云端插槽，OpenAI 兼容端点（本次未实测）
"""
