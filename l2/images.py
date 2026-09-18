"""图文分支：图片落到本地副本 → 骨架 md（正文由 agent 读图后补齐）。

为什么复制一份而不是引用原图
--------------------------
原图可能在下载目录或压缩包解压目录里，随时会被清理；复制到 `work/{id}/images/`
之后，agent 读图、用户核对都有一个稳定落点。

为什么本项目不把图片放进知识库
----------------------------
知识库规则明确「搬运 / 改名附件交给 CAL 插件或 Obsidian 界面，AI 不写脚本批量搬」。
所以这里只准备素材与占位标记，进 `8_附件/` 的动作由入库 skill 与用户完成。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from core import config as config_mod
from core import paths
from core.progress import NullReporter, ProgressReporter
from l2 import md
from l2.result import Result


def convert(
    job: dict,
    *,
    cfg: config_mod.Config,
    reporter: ProgressReporter | None = None,
    force: bool = False,
) -> Result:
    reporter = reporter or NullReporter()
    item_id = str(job["id"])
    sources = [Path(str(p)) for p in (job.get("images") or [])]
    if not sources:
        raise FileNotFoundError(f"图文素材里没有图片：{job.get('material') or item_id}")

    warnings = list(job.get("warnings") or [])
    dest_dir = paths.work_dir(item_id) / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)

    reporter.start(len(sources), "复制图片")
    copied: list[Path] = []
    for index, src in enumerate(sources, 1):
        if not src.exists():
            warnings.append(f"图片不存在，已跳过：{src}")
            reporter.update(index)
            continue
        # 加序号前缀：既避免不同子目录同名文件互相覆盖，也让页码顺序一目了然
        dest = dest_dir / f"{index:03d}_{src.name}"
        if force or not _same_file(src, dest):
            shutil.copy2(src, dest)
        copied.append(dest)
        reporter.update(index, f"{index}/{len(sources)}")
    reporter.close(f"已准备 {len(copied)} 张图片")

    if not copied:
        raise FileNotFoundError("所有图片都不存在，无法生成图文骨架。")

    text = md.render(job, md.image_body(copied), extractor="agent:vision", warnings=warnings)
    md_path = md.write(item_id, text)

    return Result(
        item_id=item_id,
        kind=str(job.get("type") or ""),
        md_path=md_path,
        extractor="agent:vision",
        image_count=len(copied),
        warnings=warnings,
    )


def _same_file(src: Path, dest: Path) -> bool:
    """已复制过且大小一致就跳过 —— 让重跑成为零成本操作。"""
    try:
        return dest.exists() and dest.stat().st_size == src.stat().st_size
    except OSError:
        return False
