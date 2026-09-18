"""本地导入层（L1）：把本地文件变成 source.json。

输入约定
--------
- 显式路径：视频文件
- 无参数时扫描「收件目录」[ingest].inbox_dir（默认 ~/Downloads/clip2obsidian）

元信息分层降级（绝不编造）
------------------------
1. sidecar 自动读：同目录同名 .json（yt-dlp info.json）/ .url
2. --url 轻量增强：yt-dlp --dump-single-json --skip-download（只取元信息，绝不下载）
3. 文件名解析：B站标题后缀、BV 号
4. 拿不到就留空，绝不从外部知识补
"""
