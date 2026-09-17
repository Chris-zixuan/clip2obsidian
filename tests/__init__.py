"""clip2obsidian 测试包。

把项目根加入 sys.path，使下面两种跑法都能直接工作：

    python -m pytest tests -q
    python -m unittest discover -s tests -t .

零新增依赖：测试只用标准库 unittest（pytest 也能收集）。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
