"""测试全局隔离：绝不触碰项目的真实 raw/ 与 work/。

为什么需要
----------
`paths.raw_dir()` / `paths.work_path()` 默认落在项目根的 `raw/`、`work/` 下。
测试若直接使用它们，就会在使用者的真实工作目录里创建产物；而用例清理时会
`rmtree` 掉自己创建的目录。两者一叠加，一旦用例输入与真实数据产生相同的条目 id
（例如用例里用了使用者正在处理的同一个视频），使用者的真实产物就会被静默删除 ——
这不是假设，实测发生过。

因此用 autouse fixture 把这两个目录整体重定向到临时目录，从根上杜绝误删。
`raw_dir()` / `work_path()` 读的是模块级全局变量，patch 即生效，被测代码零改动。

注：受管控环境里跑 pytest 可能无法创建 `pytest-of-*`，需显式给可写的 basetemp：

    pytest --basetemp=/tmp/c2o_pytest
"""

from __future__ import annotations

import pytest

from core import paths


@pytest.fixture(autouse=True)
def isolate_project_dirs(tmp_path_factory, monkeypatch):
    """把 raw/ 与 work/ 重定向到一次性的临时目录（所有用例自动生效）。"""
    root = tmp_path_factory.mktemp("c2o_isolated")
    raw = root / "raw"
    work = root / "work"
    raw.mkdir()
    work.mkdir()

    monkeypatch.setattr(paths, "RAW_DIR", raw)
    monkeypatch.setattr(paths, "WORK_DIR", work)
    yield raw, work
