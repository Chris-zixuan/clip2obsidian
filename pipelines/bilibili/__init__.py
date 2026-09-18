"""B站 pipeline：视频类内容，走字幕优先 → ASR 转写。

与抖音共用 pipelines/common.py 的提取逻辑，差异仅在元信息来源（本地文件名 /
sidecar / --url）。新增视频平台时，复制本文件、改 PLATFORM 与极少差异即可。
"""
