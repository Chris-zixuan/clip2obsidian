"""测试全局隔离：绝不触碰项目的真实 raw/ 与 work/。

为什么需要这个文件
------------------
`paths.raw_dir()` / `paths.work_path()` 默认落在项目根的 `raw/`、`work/` 下。
测试若直接使用它们，就会在**使用者的真实工作目录**里创建产物；而用例的
清理逻辑通常会 `rmtree` 掉自己创建的目录。

两者一叠加就出事：一旦用例的输入与使用者的真实数据产生**相同的 clip id**
（例如用例里用了使用者正在处理的同一个视频文件名），清理逻辑就会删掉
使用者的真实产物。这不是假设——实测发生过：跑一次 pytest，
`raw/bilibili_b06eadb7c30e/` 被静默删除。

因此用 autouse fixture 把这两个目录整体重定向到临时目录，从根上杜绝误删。

实现要点
--------
`paths.raw_dir()` / `paths.work_path()` 读的是**模块级全局**变量，所以
patch `paths.RAW_DIR` / `paths.WORK_DIR` / `paths.BACKUP_DIR` 即可生效，
被测代码零改动。

补充（2026-09-18 踩到）：在受管控的沙盒里跑 pytest 时，系统临时目录可能不允许
创建 `pytest-of-*`，会报 `PermissionError: EEXIST: file already exists, mkdir
'/private/var/folders/.../pytest-of-unknown'` 并让全部用例报错。这**不是**代码问题，
显式给一个可写的 basetemp 即可：

    pytest --basetemp=/tmp/c2o_pytest
"""

from __future__ import annotations

import pytest

from core import paths


@pytest.fixture(autouse=True)
def isolate_project_dirs(tmp_path_factory, monkeypatch):
    """把 raw/、work/、.workbuddy/backups/ 重定向到一次性的临时目录。

    autouse：所有测试自动生效，避免有人新增用例时忘记隔离而重新引入误删。
    """
    root = tmp_path_factory.mktemp("c2o_isolated")
    raw = root / "raw"
    work = root / "work"
    backups = root / "backups"
    raw.mkdir()
    work.mkdir()
    backups.mkdir()

    monkeypatch.setattr(paths, "RAW_DIR", raw)
    monkeypatch.setattr(paths, "WORK_DIR", work)
    # 渲染层替换同名笔记前会 mv 到备份区；不隔离就会往使用者的
    # 真实 .workbuddy/backups/ 里堆 `测试标题.<时间戳>.md` 垃圾。
    monkeypatch.setattr(paths, "BACKUP_DIR", backups)
    yield raw, work
