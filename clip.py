#!/usr/bin/env python3
"""clip2obsidian · 本地文件 → Obsidian 笔记

四层流水线，每层产物落盘、可单独检查、可单独重跑：

  L1  ingest   本地文件      → raw/{id}/source.json（大媒体不复制，记绝对路径）
  L2  extract  source        → work/{id}.clip.json（统一契约）
  L3  distill  clip.json     → work/{id}.digest.md（由 agent 完成）
  L4  publish  clip + digest → vault 笔记

代码不抓链接、不下载——用户把文件下到本地后直接 ingest。

常用：
  python clip.py ingest "~/Downloads/xxx_哔哩哔哩_bilibili.mp4"   # L1
  python clip.py extract bilibili:BV1xxxx                        # L2
  python clip.py publish bilibili:BV1xxxx --tags 工业 \\
      --title "认识 MAF" --digest work/bilibili_BV1xxxx.digest.md
  python clip.py scan                                            # 看收件目录
  python clip.py doctor                                          # 环境自检
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core import config as config_mod  # noqa: E402
from core import paths, registry, schema  # noqa: E402
from pipelines.common import ExtractError  # noqa: E402
from pipelines.local.ingest import IngestError  # noqa: E402
from publish.render import PublishError  # noqa: E402
from asr.base import AsrError  # noqa: E402

COMMANDS = ("run", "ingest", "extract", "publish", "status", "doctor", "scan")

_LINE = "─" * 62


# ------------------------------------------------------------------ 入口
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 裸参数（路径）一律当作 run（= ingest + extract），省去记子命令。
    # 但 -h / --help 必须放行给顶层 parser：否则只会显示 run 的参数帮助，
    # 使用者连有哪些子命令都看不到 —— 帮助的可发现性优先于省字。
    wants_top_help = argv[:1] in (["-h"], ["--help"])
    if not wants_top_help and (not argv or argv[0] not in COMMANDS):
        argv = ["run", *argv]

    ap = argparse.ArgumentParser(
        prog="clip", description="clip2obsidian · 本地文件 → Obsidian 笔记"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="L1+L2：导入本地文件并提取 clip.json")
    p_run.add_argument("paths", nargs="*", help="视频文件；省略则扫收件目录")
    p_run.add_argument("--url", default=None, help="可选链接，仅用于元信息增强（不下载）")
    p_run.add_argument("--force", action="store_true", help="忽略缓存重新导入")

    p_ingest = sub.add_parser("ingest", help="L1：本地文件 → source.json")
    p_ingest.add_argument("paths", nargs="*", help="视频文件；省略则扫收件目录")
    p_ingest.add_argument("--url", default=None, help="可选链接，仅用于元信息增强（不下载）")
    p_ingest.add_argument("--force", action="store_true", help="忽略缓存重新导入")

    p_ext = sub.add_parser("extract", help="L2：source.json → clip.json")
    p_ext.add_argument("targets", nargs="+", help="clip id（bilibili:BV1xx）或 source.json 路径")
    p_ext.add_argument("--force", action="store_true")

    p_pub = sub.add_parser("publish", help="L4：clip.json → vault 笔记")
    p_pub.add_argument("target", help="clip id（bilibili:BV1xx）或 clip.json 路径")
    p_pub.add_argument("--tags", required=True, help="受控词表标签，逗号分隔")
    p_pub.add_argument("--digest", type=Path, help="摘要 markdown 路径（默认 work/{id}.digest.md）")
    p_pub.add_argument("--title", default="", help="覆写标题（同时决定文件名）")
    p_pub.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不落盘")

    sub.add_parser("status", help="列出已产出的 clip")
    sub.add_parser("scan", help="列出收件目录里可处理的内容（不落盘）")
    sub.add_parser("doctor", help="环境自检")

    args = ap.parse_args(argv)

    try:
        cfg = config_mod.get()
    except config_mod.ConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1

    handlers = {
        "run": _cmd_run,
        "ingest": _cmd_ingest,
        "extract": _cmd_extract,
        "publish": _cmd_publish,
        "status": _cmd_status,
        "doctor": _cmd_doctor,
        "scan": _cmd_scan,
    }
    try:
        return handlers[args.cmd](args, cfg)
    except (
        config_mod.ConfigError,
        registry.UnsupportedPlatform,
        IngestError,
        ExtractError,
        PublishError,
        AsrError,
    ) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1


# ------------------------------------------------------------------ L1
def _cmd_ingest(args, cfg, *, also_extract: bool = False) -> int:
    """本地文件 → source.json（L1）。also_extract=True 时继续跑 L2。"""
    from pipelines.local import ingest as local_ingest

    targets = _collect_paths(args, cfg)
    if not targets:
        return 0

    rc = 0
    for raw in targets:
        try:
            source = local_ingest.ingest(
                raw, url=args.url, cfg=cfg, force=args.force
            )
            tag = "命中缓存" if source.get("_cache_hit") else "已导入"
            native = source.get("native_id") or "无 BV 号"
            print(f"{_LINE}\n[L1] {source['platform']} 导入：{raw}")
            print(f"[L1] {tag}｜{native}｜source.json → {source.get('_source_path')}")

            if also_extract:
                source_path = Path(source["_source_path"])
                spec = registry.get(source["platform"])
                ext_mod = _platform_module(spec, "extract")
                print("[L2] 提取中…")
                try:
                    clip = ext_mod.extract(source_path, cfg=cfg, force=args.force)
                    _print_clip_summary(clip)
                except (ExtractError, AsrError) as e:
                    # L2 跑不通（如 ASR 未就绪）可接受：L1 已落盘，稍后单独 extract
                    print(
                        f"[警告] L2 提取未跑通（可稍后单独跑 extract）：{e}",
                        file=sys.stderr,
                    )
        except (IngestError, ExtractError, AsrError) as e:
            print(f"[错误] {e}", file=sys.stderr)
            rc = 1
            continue
    return rc


def _cmd_extract(args, cfg) -> int:
    for target in args.targets:
        source_path = _resolve_source(target)
        source = _read_json(source_path)
        spec = registry.get(source.get("platform") or _platform_from_path(source_path))
        module = _platform_module(spec, "extract")
        print(f"{_LINE}\n[L2] {spec.label} 提取：{source_path.name}")
        clip = module.extract(source_path, cfg=cfg, force=args.force)
        _print_clip_summary(clip)
    return 0


def _cmd_run(args, cfg) -> int:
    """裸参数 / run：导入本地文件并尽量提取出 clip.json。"""
    return _cmd_ingest(args, cfg, also_extract=True)


# ------------------------------------------------------------------ L4
def _cmd_publish(args, cfg) -> int:
    from publish import render as render_mod

    clip_path = _resolve_clip(args.target)
    clip = schema.Clip.load(clip_path)

    digest = ""
    digest_path = args.digest or paths.work_path(clip.id, ".digest.md")
    if digest_path.exists():
        digest = digest_path.read_text(encoding="utf-8")
    elif args.digest:
        print(f"[错误] 指定的摘要文件不存在：{digest_path}", file=sys.stderr)
        return 1
    else:
        print("[提示] 未找到摘要文件，将只输出转写全文。")

    tags = [t.strip() for t in re.split(r"[,，]", args.tags) if t.strip()]

    print(f"{_LINE}\n[L4] 入库：{clip.id}")
    if args.dry_run:
        print("[L4] dry-run，不落盘")

    result = render_mod.render(
        clip, tags=tags, digest=digest, title=args.title, cfg=cfg, dry_run=args.dry_run
    )

    print(f"[L4] 笔记：{result.path}")
    for w in result.warnings:
        print(f"[!] {w}")
    if not args.dry_run:
        print(f"[L4] 完成｜{len(tags)} 个标签：{', '.join(tags)}")
    return 0


# ------------------------------------------------------------------ 辅助命令
def _cmd_status(args, cfg) -> int:
    files = sorted(paths.WORK_DIR.glob("*.clip.json"))
    if not files:
        print("还没有任何 clip。先跑：python clip.py ingest <本地文件路径>")
        return 0
    print(f"{_LINE}\n{'clip id':<34}{'形态':<11}{'摘要':<6}标题")
    print(_LINE)
    for p in files:
        try:
            clip = schema.Clip.load(p)
        except Exception as e:
            print(f"{p.name:<34}(解析失败：{e})")
            continue
        has_digest = "有" if paths.work_path(clip.id, ".digest.md").exists() else "—"
        title = clip.meta.title or "(无标题)"
        print(f"{clip.id:<34}{clip.content_type:<11}{has_digest:<6}{title[:28]}")
    return 0


def _cmd_doctor(args, cfg) -> int:
    print(f"{_LINE}\nclip2obsidian 环境自检")
    print(f"平台：{paths.PLATFORM_TAG}｜项目根：{paths.PROJECT_ROOT}")
    print(_LINE)

    ok = True
    for k, v in config_mod.describe(cfg):
        print(f"  {k:<16}{v}")

    print(_LINE)
    checks: list[tuple[str, bool, str]] = [
        ("知识库目录存在", cfg.vault.root.exists(), str(cfg.vault.root)),
        ("剪藏落点存在", cfg.vault.inbox.exists(), str(cfg.vault.inbox)),
        ("ffmpeg 可用", bool(cfg.tools.ffmpeg_bin),
         cfg.tools.ffmpeg_bin or "未找到，brew install ffmpeg"),
        ("Python 解释器", Path(cfg.tools.python_bin).exists(), cfg.tools.python_bin),
        ("项目目录可写", _writable(paths.WORK_DIR), str(paths.WORK_DIR)),
    ]

    py = cfg.tools.python_bin
    probe = _probe([py, "-c", "import faster_whisper; print('已安装')"])
    checks.append(("faster-whisper 可用", bool(probe), probe or f"{py} 里没有 faster-whisper"))

    zh = _probe([py, "-c", "import zhconv; print('已安装')"])
    checks.append(("zhconv 可用（繁转简）", bool(zh), zh or f"{py} 里没有 zhconv"))

    for name, passed, detail in checks:
        print(f"  {'✓' if passed else '✗'} {name:<26}{detail if not passed else ''}")
        if not passed:
            ok = False

    # yt-dlp 仅用于 --url 元信息增强，不计入成败
    ytdlp = _probe([py, "-m", "yt_dlp", "--version"])
    print(f"  {'✓' if ytdlp else '·'} yt-dlp 可用（仅 --url 增强需要）"
          f"{('：' + ytdlp) if ytdlp else '：未安装也不影响本地导入'}")

    print(_LINE)
    print("平台注册：" + "、".join(f"{p.name}({p.label})" for p in registry.PLATFORMS))
    print("状态：" + ("全部通过，可以开工。" if ok else "有项目未通过，见上面 ✗ 行。"))
    return 0 if ok else 1


def _cmd_scan(args, cfg) -> int:
    """列出收件目录里可识别的待处理项（不落盘）。"""
    from pipelines.local import ingest as local_ingest

    items = local_ingest.scan(cfg.ingest.inbox)
    if not items:
        print(f"收件目录 {cfg.ingest.inbox} 为空或不存在，没有可处理的内容。")
        print(f"  可在 config.toml 的 [ingest].inbox_dir 配置，或显式传路径给 ingest。")
        return 0
    print(f"{_LINE}\n收件目录：{cfg.ingest.inbox}")
    print(_LINE)
    for path, spec, note in items:
        print(f"  {note:<12} {path.name}")
    print(_LINE)
    print("用 clip.py ingest <路径> 导入，或直接 clip.py <路径> 一步到位（L1+L2）。")
    return 0


# ------------------------------------------------------------------ 工具
def _collect_paths(args, cfg) -> list[str]:
    """决定这一轮处理哪些路径。

    命令行显式给了路径就用它；省略则扫「收件目录」。
    识别不出平台的内容不静默丢弃，而是明确报出来：本地文件名千奇百怪，
    让使用者知道「这一项我没认出来」远比悄悄跳过有用。
    """
    if args.paths:
        return list(args.paths)

    from pipelines.local import ingest as local_ingest

    print(f"{_LINE}\n未指定文件，改扫收件目录：{cfg.ingest.inbox}")
    items = local_ingest.scan(cfg.ingest.inbox)

    known = [p for p, spec, _ in items if spec]
    unknown = [p for p, spec, _ in items if not spec]
    if unknown:
        print(f"[提示] {len(unknown)} 项识别不出平台，已跳过：")
        for p in unknown[:5]:
            print(f"       {p.name}")
        if len(unknown) > 5:
            print(f"       …另有 {len(unknown) - 5} 项")

    if not known:
        print("[提示] 收件目录里没有可处理的内容。")
        print(f"       把下载好的文件放进 {cfg.ingest.inbox}，或直接给路径：clip.py ingest <路径>")
        print("       先看看目录里有什么：clip.py scan")
        return []

    print(f"[L1] 收件目录命中 {len(known)} 项")
    return [str(p) for p in known]


def _platform_module(spec: registry.PlatformSpec, layer: str):
    try:
        return importlib.import_module(f"pipelines.{spec.module}.{layer}")
    except ImportError as e:
        raise registry.UnsupportedPlatform(
            f"平台 {spec.name} 的 {layer} 层还没实现：{e}"
        ) from e


def _resolve_source(target: str) -> Path:
    """接受 clip id 或 source.json 路径。"""
    if ":" in target and not target.endswith(".json"):
        p = paths.raw_dir(target) / "source.json"
    else:
        p = Path(target).expanduser()
    if not p.exists():
        raise SystemExit(f"[错误] 找不到采集结果：{p}\n  先跑 L1：python clip.py ingest <路径>")
    return p


def _resolve_clip(target: str) -> Path:
    if ":" in target and not target.endswith(".json"):
        p = paths.work_path(target, ".clip.json")
    else:
        p = Path(target).expanduser()
    if not p.exists():
        raise SystemExit(f"[错误] 找不到 clip：{p}\n  先跑 L1+L2：python clip.py <路径>")
    return p


def _platform_from_path(p: Path) -> str:
    name = p.parent.name
    return name.split("_", 1)[0] if "_" in name else name


def _read_json(p: Path) -> dict[str, object]:
    import json

    return json.loads(p.read_text(encoding="utf-8"))


def _print_clip_summary(clip: schema.Clip) -> None:
    text = clip.full_text()
    print(f"[L2] 完成｜{clip.content_type}｜{len(text)} 字｜转写 {len(clip.content.transcript)} 段")
    print(f"     extractor: {clip.provenance.extractor}")
    print(f"     clip.json: {paths.work_path(clip.id, '.clip.json')}")
    print(f"[→] 下一步（L3）：写摘要 → work/{clip.id.replace(':', '_')}.digest.md")
    print(f"[→] 最后（L4）：python clip.py publish {clip.id} --tags <标签>")


def _probe(cmd: list[str]) -> str:
    import subprocess

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip().splitlines()[0] if proc.stdout else "ok"


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
