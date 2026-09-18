#!/usr/bin/env python3
"""clip2obsidian · 本地素材 → 待入库的 md

本项目只做三层里的**前两层**，L3 入库由配套 skill + agent 完成：

  L1 route    文件 / 目录 / zip  → raw/{id}/job.json
  L2 convert  job.json           → work/{id}.md（纯正文 + 来源块）
  L3 ingest   （不在本项目）      agent 读 md，用 skills/clip-to-obsidian 落库

常用：
  python clip.py ~/Downloads/xxx.mp4        # 一步跑完 L1+L2
  python clip.py scan                        # 看收件目录里有什么
  python clip.py status                      # 看处理到哪一步了
  python clip.py doctor                      # 环境自检
  python clip.py convert --progress window   # 转写时用独立窗口看进度
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core import config as config_mod  # noqa: E402
from core import paths, route  # noqa: E402
from core.textnorm import to_simplified  # noqa: E402
from l1 import ingest as l1_ingest  # noqa: E402
from l1.ziputil import ArchiveError  # noqa: E402
from l2 import convert as l2_convert  # noqa: E402
from l2.media import MediaError  # noqa: E402
from l2.transcribe.base import TranscribeError  # noqa: E402

COMMANDS = ("run", "route", "convert", "status", "scan", "doctor")
_LINE = "─" * 62
_PROGRESS_CHOICES = ("inline", "window", "off")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 裸参数（路径）一律当作 run（= 登记 + 转换），省去记子命令。
    # 但 -h / --help 必须放行：否则只显示 run 的帮助，使用者连有哪些子命令都看不到。
    wants_help = argv[:1] in (["-h"], ["--help"])
    if not wants_help and (not argv or argv[0] not in COMMANDS):
        argv = ["run", *argv]

    ap = argparse.ArgumentParser(
        prog="clip", description="clip2obsidian · 本地素材 → 待入库的 md"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common_input(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("paths", nargs="*", help="文件 / 目录 / zip；省略则扫收件目录")
        parser.add_argument("--url", default=None, help="可选链接，只用于元信息增强（不下载）")
        parser.add_argument("--force", action="store_true", help="忽略缓存重新处理")
        parser.add_argument("--progress", choices=_PROGRESS_CHOICES, default=None,
                            help="进度展示：inline（默认）/ window / off")

    p_run = sub.add_parser("run", help="L1+L2：登记并转换")
    add_common_input(p_run)

    p_route = sub.add_parser("route", help="L1：只登记素材，产出 job.json")
    add_common_input(p_route)

    p_conv = sub.add_parser("convert", help="L2：按 job 产出 md")
    p_conv.add_argument("targets", nargs="*", help="条目 id；省略则处理全部已登记项")
    p_conv.add_argument("--force", action="store_true", help="忽略缓存重新处理")
    p_conv.add_argument("--progress", choices=_PROGRESS_CHOICES, default=None,
                        help="进度展示：inline（默认）/ window / off")

    sub.add_parser("status", help="列出已登记素材与产物状态")
    sub.add_parser("scan", help="列出收件目录里的内容（不落盘）")
    sub.add_parser("doctor", help="环境自检")

    args = ap.parse_args(argv)

    try:
        cfg = config_mod.get()
    except config_mod.ConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1

    handlers = {
        "run": _cmd_run,
        "route": _cmd_route,
        "convert": _cmd_convert,
        "status": _cmd_status,
        "scan": _cmd_scan,
        "doctor": _cmd_doctor,
    }
    try:
        return handlers[args.cmd](args, cfg)
    except (
        config_mod.ConfigError,
        route.RouteError,
        l1_ingest.IngestError,
        ArchiveError,
        l2_convert.ConvertError,
        MediaError,
        TranscribeError,
    ) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1


# ------------------------------------------------------------------ L1
def _cmd_route(args, cfg) -> int:
    inputs = _collect_inputs(args.paths, cfg)
    rc = 0
    for raw in inputs:
        try:
            jobs = l1_ingest.register(raw, cfg=cfg, url=args.url, force=args.force)
        except (l1_ingest.IngestError, ArchiveError, route.RouteError) as e:
            print(f"[错误] {e}", file=sys.stderr)
            rc = 1
            continue
        for job in jobs:
            _print_job(job)
    return rc


def _cmd_run(args, cfg) -> int:
    inputs = _collect_inputs(args.paths, cfg)
    rc = 0
    for raw in inputs:
        try:
            jobs = l1_ingest.register(raw, cfg=cfg, url=args.url, force=args.force)
        except (l1_ingest.IngestError, ArchiveError, route.RouteError) as e:
            print(f"[错误] {e}", file=sys.stderr)
            rc = 1
            continue
        for job in jobs:
            _print_job(job)
            if not _convert_one(job, cfg, args.progress, args.force):
                rc = 1
    return rc


# ------------------------------------------------------------------ L2
def _cmd_convert(args, cfg) -> int:
    jobs = _select_jobs(args.targets)
    if not jobs:
        print("没有已登记的素材。先跑：python clip.py route <路径>")
        return 0
    rc = 0
    for job in jobs:
        if not _convert_one(job, cfg, args.progress, args.force):
            rc = 1
    return rc


def _convert_one(job: dict[str, object], cfg, progress_mode: str | None, force: bool) -> bool:
    item_id = str(job.get("id"))
    print(f"{_LINE}\n[L2] 转换 {item_id}（{job.get('type')}）")
    try:
        result = l2_convert.convert(
            job, cfg=cfg, progress_mode=progress_mode, force=force
        )
    except (l2_convert.ConvertError, MediaError, TranscribeError, OSError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return False

    if result.image_count:
        print(f"[L2] 图片 {result.image_count} 张 → {result.md_path.parent / 'images'}")
    if result.segment_count:
        print(f"[L2] 转写 {result.segment_count} 段（{result.extractor}）")
    print(f"[L2] 产物：{result.md_path}")
    for warning in result.warnings:
        print(f"[!] {warning}")
    print("[→] 下一步（L3）：让 agent 读该 md，用 skills/clip-to-obsidian 落库")
    return True


# ------------------------------------------------------------------ 辅助
def _collect_inputs(raw_paths: list[str], cfg) -> list[str]:
    """决定这一轮处理哪些输入。

    显式给了路径就用它；省略则扫收件目录。识别不出的项明确报出来 ——
    让用户知道「这一项我没认出来」远比静默跳过有用。
    """
    if raw_paths:
        return list(raw_paths)

    print(f"{_LINE}\n未指定输入，改扫收件目录：{cfg.ingest.inbox}")
    items = l1_ingest.scan(cfg.ingest.inbox)
    known = [str(p) for p, kind, _ in items if kind]
    unknown = [p for p, kind, _ in items if not kind]

    for path in unknown[:5]:
        print(f"[提示] 识别不出类型，已跳过：{path.name}")
    if len(unknown) > 5:
        print(f"[提示] 另有 {len(unknown) - 5} 项未识别")

    if not known:
        print("[提示] 收件目录里没有可处理的素材。")
        print(f"       把文件放进 {cfg.ingest.inbox}，或直接给路径：clip.py <路径>")
        return []
    print(f"[L1] 收件目录命中 {len(known)} 项")
    return known


def _select_jobs(targets: list[str]) -> list[dict[str, object]]:
    jobs = l1_ingest.load_jobs()
    if not targets:
        return jobs

    wanted = {t.strip() for t in targets if t.strip()}
    selected = [j for j in jobs if j["id"] in wanted]
    missing = wanted - {j["id"] for j in selected}
    for item in sorted(missing):
        print(f"[提示] 没有这个条目（{item}）。可先跑 status 查看已登记项。",
              file=sys.stderr)
    return selected


def _print_job(job: dict[str, object]) -> None:
    title = (job.get("meta") or {}).get("title") or "(无标题)"
    tag = "命中登记" if job.get("_cache_hit") else "已登记"
    print(f"{_LINE}\n[L1] {tag}｜{job.get('type')}｜{job['id']}｜{title[:28]}")
    for warning in job.get("warnings") or []:
        print(f"[!] {warning}")


def _cmd_status(args, cfg) -> int:
    jobs = l1_ingest.load_jobs()
    if not jobs:
        print("还没有登记任何素材。先跑：python clip.py <路径>")
        return 0

    print(f"{_LINE}\n{'条目 id':<14}{'类型':<11}{'md':<4}{'图片':<5}标题")
    print(_LINE)
    for job in jobs:
        item_id = job["id"]
        md_path = paths.work_path(item_id, ".md")
        images = len(job.get("images") or [])
        title = ((job.get("meta") or {}).get("title") or "(无标题)")[:30]
        print(
            f"{item_id:<14}{str(job.get('type')):<11}"
            f"{('有' if md_path.exists() else '—'):<4}"
            f"{(str(images) if images else '—'):<5}{title}"
        )
    print(_LINE)
    print("md 在 work/ 下；入库由 agent + skills/clip-to-obsidian 完成。")
    return 0


def _cmd_scan(args, cfg) -> int:
    items = l1_ingest.scan(cfg.ingest.inbox)
    if not items:
        print(f"收件目录为空或不存在：{cfg.ingest.inbox}")
        print("  可在 config.toml 的 [ingest].inbox_dir 配置，或直接传路径给 clip.py")
        return 0

    print(f"{_LINE}\n收件目录：{cfg.ingest.inbox}")
    print(_LINE)
    for path, kind, note in items:
        print(f"  {'✓' if kind else '·'} {note:<18}{path.name}")
    print(_LINE)
    print("用 clip.py <路径> 处理，或直接 clip.py（扫描整个收件目录）。")
    return 0


def _cmd_doctor(args, cfg) -> int:
    from l2.transcribe import local as local_transcribe

    print(f"{_LINE}\nclip2obsidian 环境自检")
    print(f"平台：{paths.PLATFORM_TAG}｜项目根：{paths.PROJECT_ROOT}")
    print(_LINE)
    for key, value in config_mod.describe(cfg):
        print(f"  {key:<14}{value}")

    print(_LINE)
    checks: list[tuple[str, bool, str]] = [
        ("Python ≥ 3.11", sys.version_info >= (3, 11), f"当前 {sys.version.split()[0]}"),
        ("收件目录存在", cfg.ingest.inbox.exists(), str(cfg.ingest.inbox)),
        ("ffmpeg 可用", bool(cfg.tools.ffmpeg_bin),
         cfg.tools.ffmpeg_bin or "未找到（brew install ffmpeg）"),
        ("ffprobe 可用", bool(cfg.tools.ffprobe_bin),
         cfg.tools.ffprobe_bin or "未找到（通常与 ffmpeg 同目录）"),
        ("本地转写引擎", _local_ok(cfg), local_transcribe.describe(cfg)),
        ("项目目录可写", _writable(paths.WORK_DIR), str(paths.WORK_DIR)),
    ]
    for name, passed, detail in checks:
        mark = "✓" if passed else "✗"
        print(f"  {mark} {name:<16}{'' if passed else detail}")
    print(f"  · 云端插槽{'已配置' if cfg.transcribe.cloud.configured else '未配置（仅本地）'}")

    ok = all(passed for _, passed, _ in checks)
    print(_LINE)
    print("状态：" + ("全部通过，可以开工。" if ok else "有项目未通过，见上面 ✗ 行。"))
    return 0 if ok else 1


def _local_ok(cfg) -> bool:
    from l2.transcribe import local as local_transcribe

    ok, _ = local_transcribe.PROVIDER.available(cfg)
    return ok


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
