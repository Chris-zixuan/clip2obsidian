"""L2 转换层：把素材变成纯正文 md。

- `convert.py`：按素材类型分派到三条分支
- `video.py` / `audio.py` / `images.py`：三条分支各自的处理
- `media.py`：ffprobe 取时长、ffmpeg 抽音轨（带进度）
- `subtitle.py`：本地字幕解析（srt / vtt / ass）
- `md.py`：md 组装（来源块 + 正文）
- `transcribe/`：转写 provider（本地 mlx/faster、云端 OpenAI 兼容插槽）
"""
