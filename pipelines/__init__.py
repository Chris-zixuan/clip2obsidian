"""平台 pipeline 包。

- `local/`：L1 本地导入（产出与下游约定的 source.json）
- `common.py`：视频类平台共享的提取逻辑（字幕解析 / ASR / 元信息映射）
- 每个平台一个子目录，内含 `extract.py`（L2）
"""
