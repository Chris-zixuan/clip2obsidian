"""L2 产物描述：一条素材转换完成后的结果摘要。

单独成文件是为了避免循环导入 —— 三条分支（video / audio / images）都返回它，
分派器（convert.py）消费它，谁都不必 import 谁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Result:
    """一条素材的转换结果。"""

    item_id: str
    kind: str
    md_path: Path
    extractor: str = ""          # 如 whisper:mlx:large-v3 / subtitle:local:srt
    segment_count: int = 0       # 转写段数（图文为 0）
    image_count: int = 0         # 图片数（视频 / 音频为 0）
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_agent(self) -> bool:
        """图文需要 agent 逐张读图补正文；其它类型已可直接入库。"""
        return self.kind == "image_set"
