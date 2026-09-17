#!/usr/bin/env python3
"""clip2obsidian · 链接 → Obsidian 笔记

四层流水线，每层产物落盘、可单独检查、可单独重跑：

  L1  fetch    链接          → raw/{id}/source.json + 媒体物料
  L2  extract  source        → work/{id}.clip.json      （统一契约）
  L3  distill  clip.json     → work/{id}.digest.md      （由 agent 完成）
  L4  publish  clip + digest → vault 笔记 + 附件

常用：
  python clip.py "https://v.douyin.com/xxxxxx/"                 # L1 + L2
  python clip.py publish douyin:123 --tags 生活 \\
      --digest work/douyin_123.digest.md --title "精简标题"
  python clip.py doctor                                         # 环境自检
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

COMMANDS = ("run", "fetch", "extract", "publish", "status", "doctor")

_LINE = "─" * 62


# ------------------------------------------------------------------ 入口
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 裸链接或空参数一律当作 run，省去记子命令
    if not argv or argv[0] not in COMMANDS:
        argv = ["run", *argv]

    ap = argparse.ArgumentParser(
        prog="clip", description="clip2obsidian · 链接 → Obsidian 笔记"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="L1+L2：抓取并提取出 clip.json")
    p_run.add_argument("links", nargs="+", help="链接或含链接的分享文案")
    p_run.add_argument("--force", action="store_true", help="忽略缓存重新抓取")

    p_fetch = sub.add_parser("fetch", help="L1：只抓原始物料")
    p_fetch.add_argument("links", nargs="+")
    p_fetch.add_argument("--force", action="store_true")

    p_ext = sub.add_parser("extract", help="L2：物料 → clip.json")
    p_ext.add_argument("targets", nargs="+", help="clip id（douyin:123）或 source.json 路径")
    p_ext.add_argument("--force", action="store_true")

    p_pub = sub.add_parser("publish", help="L4：clip.json → vault 笔记")
    p_pub.add_argument("target", help="clip id（douyin:123）或 clip.json 路径")
    p_pub.add_argument("--tags", required=True, help="受控词表标签，逗号分隔")
    p_pub.add_argument("--digest", type=Path, help="摘要 markdown 路径（默认自动找 work/{id}.digest.md）")
    p_pub.add_argument("--title", default="", help="覆写标题（同时决定文件名）")
    p_pub.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不落盘")

    sub.add_parser("status", help="列出已产出的 clip")
    sub.add_parser("doctor", help="环境自检")

    args = ap.parse_args(argv)

    try:
        cfg = config_mod.get()
    except config_mod.ConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1

    handlers = {
        "run": _cmd_run,
        "fetch": _cmd_fetch,
        "extract": _cmd_extract,
        "publish": _cmd_publish,
        "status": _cmd_status,
        "doctor": _cmd_doctor,
    }
    try:
        return handlers[args.cmd](args, cfg)
    except (
        config_mod.ConfigError,
        registry.UnsupportedLink,
    ) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1
    except Exception as e:  # 各层自定义错误统一在此收敛
        if type(e).__name__ in ("FetchError", "ExtractError", "PublishError", "AsrError"):
            print(f"[错误] {e}", file=sys.stderr)
            return 1
        raise


# ------------------------------------------------------------------ L1 + L2
def _cmd_fetch(args, cfg) -> int:
    for raw in args.links:
        url = registry.extract_url(raw)
        spec = registry.detect(url)
        module = _platform_module(spec, "fetch")
        print(f"{_LINE}\n[L1] {spec.label} 采集：{url}")
        source = module.fetch(url, cfg=cfg, force=args.force)
        tag = "命中缓存" if source.get("_cache_hit") else "已抓取"
        kind = "字幕" if source.get("subtitle") else ("视频" if source.get("media") else "无媒体")
        print(f"[L1] {tag}｜物料：{kind}")
        print(f"     {source.get('_source_path')}")
    return 0


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
    rc = 0
    for raw in args.links:
        try:
            url = registry.extract_url(raw)
            spec = registry.detect(url)
            print(f"{_LINE}\n[L1] {spec.label} 采集：{url}")
            fetch_mod = _platform_module(spec, "fetch")
            source = fetch_mod.fetch(url, cfg=cfg, force=args.force)
            tag = "命中缓存" if source.get("_cache_hit") else "已抓取"
            kind = "字幕" if source.get("subtitle") else ("视频" if source.get("media") else "无媒体")
            print(f"[L1] {tag}｜物料：{kind}")

            print(f"[L2] 提取中…")
            extract_mod = _platform_module(spec, "extract")
            clip = extract_mod.extract(Path(source["_source_path"]), cfg=cfg, force=args.force)
            _print_clip_summary(clip)
        except Exception as e:
            if type(e).__name__ in ("FetchError", "ExtractError", "AsrError"):
                print(f"[错误] {e}", file=sys.stderr)
                rc = 1
                continue
            raise
    return rc


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
    if result.assets:
        print(f"[L4] 附件 {len(result.assets)} 个 → {result.assets[0].parent}")
    for w in result.warnings:
        print(f"[!] {w}")
    if not args.dry_run:
        print(f"[L4] 完成｜{len(tags)} 个标签：{', '.join(tags)}")
    return 0


# ------------------------------------------------------------------ 辅助命令
def _cmd_status(args, cfg) -> int:
    files = sorted(paths.WORK_DIR.glob("*.clip.json"))
    if not files:
        print("还没有任何 clip。先跑：python clip.py <链接>")
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
    checks: list[tuple[str, bool, str]] = []

    vault = cfg.vault.root
    checks.append(("知识库目录存在", vault.exists(), str(vault)))

    inbox = cfg.vault.inbox
    checks.append(("剪藏落点存在", inbox.exists(), str(inbox)))

    ffmpeg = cfg.tools.ffmpeg_bin
    checks.append(("ffmpeg 可用", bool(ffmpeg), ffmpeg or "未找到，brew install ffmpeg"))

    py = cfg.tools.python_bin
    checks.append(("Python 解释器", Path(py).exists(), py))

    ytdlp = _probe([py, "-m", "yt_dlp", "--version"])
    checks.append(("yt-dlp 可用", bool(ytdlp), ytdlp or f"{py} -m yt_dlp 不可用"))

    if cfg.asr.backend == "faster":
        mod, label = "faster_whisper", "faster-whisper"
    elif cfg.asr.backend == "mlx":
        mod, label = "mlx_whisper", "mlx-whisper"
    else:
        mod, label = None, cfg.asr.backend
    if mod:
        probe = _probe([py, "-c", f"import {mod}; print('已安装')"])
        checks.append((f"{label} 可用", bool(probe), probe or f"{py} 里没有 {label}"))

    zh = _probe([py, "-c", "import zhconv; print('已安装')"])
    checks.append(
        ("zhconv 可用（繁转简）", bool(zh), zh or f"{py} 里没有 zhconv，繁转简会被跳过")
    )
    checks.append(("项目目录可写", _writable(paths.WORK_DIR), str(paths.WORK_DIR)))

    for name, passed, detail in checks:
        mark = "✓" if passed else "✗"
        if not passed:
            ok = False
        print(f"  {mark} {name:<26}{detail if not passed else ''}")

    print(_LINE)
    print("平台注册：" + "、".join(f"{p.name}({p.label})" for p in registry.PLATFORMS))
    print("状态：" + ("全部通过，可以开工。" if ok else "有项目未通过，见上面 ✗ 行。"))
    return 0 if ok else 1


# ------------------------------------------------------------------ 工具
def _platform_module(spec: registry.PlatformSpec, layer: str):
    try:
        return importlib.import_module(f"pipelines.{spec.module}.{layer}")
    except ImportError as e:
        raise registry.UnsupportedLink(
            f"平台 {spec.name} 的 {layer} 层还没实现：{e}\n"
            f"  （v1 只承诺抖音与小红书，见架构设计 §8）"
        ) from e


def _resolve_source(target: str) -> Path:
    """接受 clip id 或 source.json 路径。"""
    if ":" in target and not target.endswith(".json"):
        p = paths.raw_dir(target) / "source.json"
    else:
        p = Path(target).expanduser()
    if not p.exists():
        raise SystemExit(f"[错误] 找不到采集结果：{p}\n  先跑 L1：python clip.py fetch <链接>")
    return p


def _resolve_clip(target: str) -> Path:
    if ":" in target and not target.endswith(".json"):
        p = paths.work_path(target, ".clip.json")
    else:
        p = Path(target).expanduser()
    if not p.exists():
        raise SystemExit(f"[错误] 找不到 clip：{p}\n  先跑 L1+L2：python clip.py <链接>")
    return p


def _platform_from_path(p: Path) -> str:
    name = p.parent.name
    return name.split("_", 1)[0] if "_" in name else name


def _read_json(p: Path) -> dict:
    import json

    return json.loads(p.read_text(encoding="utf-8"))


def _print_clip_summary(clip: schema.Clip) -> None:
    text = clip.full_text()
    print(
        f"[L2] 完成｜{clip.content_type}｜{len(text)} 字｜"
        f"转写 {len(clip.content.transcript)} 段｜图 {len([a for a in clip.content_assets if a.kind == 'image'])} 张"
    )
    print(f"     extractor: {clip.provenance.extractor}")
    print(f"     clip.json: {paths.work_path(clip.id, '.clip.json')}")
    if not clip.ocr_ready():
        print(f"[→] 下一步（L3）：读图并回填 images_ocr，然后写摘要")
    else:
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
