"""进度上报：让长任务（抽音轨 / 转写）看得见。

为什么自己写而不用进度条库
--------------------------
一是「独立窗口」模式需要把进度写到**另一个进程能读到**的地方，二是本项目尽量只用
标准库。进度本身只有一份（阶段 + 比例 + 已用 + 预计剩余），差别只在展示层：

| reporter | 展示 |
|---|---|
| `InlineReporter` | 终端单行原地刷新（默认）；非终端环境自动降级为分段日志 |
| `JsonReporter` | 原子写 `work/{id}.progress.json`，供外部进程读取 |
| `WindowReporter` | 在 Json 之上再开一个终端窗口，轮询同一份 JSON 渲染进度条 |
| `NullReporter` | 测试与 `--quiet` 使用 |

窗口渲染器就在本文件底部，用 `python -m core.progress --watch <json>` 启动。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from core import paths

BAR_WIDTH = 28
INLINE_THROTTLE_SEC = 0.2     # 终端刷新节流：再快人眼也看不清，还浪费 CPU
JSON_THROTTLE_SEC = 1.0       # 落盘节流：窗口读的是秒级精度，够用
_STALE_SEC = 30               # 窗口判定「主进程已死」的静默阈值


# ------------------------------------------------------------------ 公共工具
def format_duration(seconds: float) -> str:
    """秒 → `1:23` / `1:02:03`。未知或负数显示 `--:--`。"""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_bar(ratio: float, width: int = BAR_WIDTH) -> str:
    """比例 → 进度条字符串。比例会被夹到 [0, 1]。"""
    ratio = min(1.0, max(0.0, ratio or 0.0))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def parse_ffmpeg_progress(line: str) -> float | None:
    """解析 `ffmpeg -progress pipe:1` 的一行，返回已处理秒数；无关行返回 None。

    注意：ffmpeg 的 `out_time_ms` 因历史原因单位其实是**微秒**，与 `out_time_us`
    同值。这里两者都按微秒处理 —— 若按毫秒换算，进度会瞬间冲到 100%。
    """
    line = (line or "").strip()
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            try:
                return int(line[len(key):]) / 1_000_000
            except ValueError:
                return None
    if line.startswith("out_time="):
        parts = line[len("out_time="):].split(":")
        if len(parts) == 3:
            try:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            except ValueError:
                return None
    return None


# ------------------------------------------------------------------ 协议
class ProgressReporter(Protocol):
    """进度上报协议。所有实现都允许乱序调用（start 可省）。"""

    def start(self, total: float, label: str = "") -> None: ...
    def update(self, done: float, note: str = "") -> None: ...
    def close(self, summary: str = "") -> None: ...


class NullReporter:
    """什么都不做。测试与 `--progress off` 使用。"""

    def start(self, total: float, label: str = "") -> None:
        pass

    def update(self, done: float, note: str = "") -> None:
        pass

    def close(self, summary: str = "") -> None:
        pass


# ------------------------------------------------------------------ 快照
@dataclass
class Snapshot:
    """一份进度的可序列化形态（窗口渲染器读的就是它）。"""

    label: str = ""
    done: float = 0.0
    total: float = 0.0
    note: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed: bool = False
    summary: str = ""

    @property
    def ratio(self) -> float:
        return (self.done / self.total) if self.total > 0 else 0.0

    @property
    def elapsed(self) -> float:
        return max(0.0, self.updated_at - self.started_at)

    @property
    def eta(self) -> float:
        """预计剩余秒数；样本不足或总量未知时返回 -1（调用方显示 --:--）。"""
        ratio = self.ratio
        if self.total <= 0 or ratio <= 0.01:
            return -1.0
        return max(0.0, self.elapsed / ratio - self.elapsed)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "done": round(self.done, 3),
            "total": round(self.total, 3),
            "ratio": round(self.ratio, 4),
            "elapsed": round(self.elapsed, 1),
            "eta": round(self.eta, 1),
            "note": self.note,
            "updated_at": self.updated_at,
            "closed": self.closed,
            "summary": self.summary,
        }


# ------------------------------------------------------------------ 终端
class InlineReporter:
    """终端单行原地刷新。

    非 TTY（重定向到文件、被 IDE 捕获）时不写 `\r`，改成按 10% 里程碑打行 ——
    否则日志文件里会出现一整行被回车覆盖的乱码。
    """

    def __init__(self, stream=None, *, enabled: bool = True, width: int = BAR_WIDTH):
        self.stream = stream or sys.stderr
        self.enabled = enabled
        self.width = width
        self.snap = Snapshot()
        self._last_paint = 0.0
        self._last_bucket = -1
        self._last_len = 0   # 上一行长度：用于补空格，避免短行盖不住长行尾巴

    @property
    def is_tty(self) -> bool:
        try:
            return bool(self.stream.isatty())
        except (AttributeError, ValueError):
            return False

    def start(self, total: float, label: str = "") -> None:
        self.snap = Snapshot(label=label, total=total)
        if not self.enabled:
            return
        self._last_paint = 0.0
        self._last_bucket = -1
        self._last_len = 0
        # 只有真终端才画原地刷新的进度条；被重定向到文件时留给 update() 打里程碑行
        if self.is_tty:
            self._paint(force=True)

    def update(self, done: float, note: str = "") -> None:
        if not self.enabled:
            return
        self.snap.done = done
        self.snap.note = note
        self.snap.updated_at = time.time()

        if self.is_tty:
            if self.snap.updated_at - self._last_paint >= INLINE_THROTTLE_SEC:
                self._paint()
        else:
            bucket = int(self.snap.ratio * 10)
            if bucket > self._last_bucket:
                self._last_bucket = bucket
                self._write_line()

    def close(self, summary: str = "") -> None:
        if not self.enabled:
            return
        self.snap.done = self.snap.total
        self.snap.closed = True
        self.snap.summary = summary
        self.snap.updated_at = time.time()
        if self.is_tty:
            self._paint(force=True)
            if summary:
                self.stream.write("\n")
        if summary:
            self.stream.write(f"{summary}\n")
        self.stream.flush()

    # -------------------------------------------------------- 内部
    def _paint(self, *, force: bool = False) -> None:
        self._last_paint = time.time()
        s = self.snap
        pct = f"{s.ratio * 100:5.1f}%"
        line = (
            f"{render_bar(s.ratio, self.width)} {pct} "
            f"{format_duration(s.done)}/{format_duration(s.total)} "
            f"已用 {format_duration(s.elapsed)} 剩 {format_duration(s.eta)}"
            f"{('  ' + s.note) if s.note else ''}"
        )
        # 用 \r 回到行首并补足空格：上一条更长的进度行才不会被留下半截
        pad = max(0, self._last_len - len(line))
        self.stream.write("\r" + line + " " * pad)
        self._last_len = len(line)
        self.stream.flush()

    def _write_line(self) -> None:
        s = self.snap
        self.stream.write(
            f"[{s.ratio * 100:3.0f}%] {s.label} {format_duration(s.done)}/"
            f"{format_duration(s.total)}{('  ' + s.note) if s.note else ''}\n"
        )
        self.stream.flush()


class JsonReporter:
    """把进度原子写到 JSON 文件，供另一个进程（窗口）读取。"""

    def __init__(self, path: Path, *, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self.snap = Snapshot()
        self._last_write = 0.0

    def start(self, total: float, label: str = "") -> None:
        self.snap = Snapshot(label=label, total=total)
        self._write(force=True)

    def update(self, done: float, note: str = "") -> None:
        self.snap.done = done
        self.snap.note = note
        self.snap.updated_at = time.time()
        self._write()

    def close(self, summary: str = "") -> None:
        self.snap.done = self.snap.total or self.snap.done
        self.snap.closed = True
        self.snap.summary = summary
        self.snap.updated_at = time.time()
        self._write(force=True)

    def _write(self, *, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.time()
        if not force and now - self._last_write < JSON_THROTTLE_SEC:
            return
        self._last_write = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self.snap.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, self.path)   # 原子替换：窗口不会读到写了一半的 JSON
        except OSError:
            pass                         # 进度写不进去不该让主流程失败


class WindowReporter(JsonReporter):
    """在独立终端窗口里显示进度条；打不开窗口时自动回退就地进度。

    非 macOS 或 `open` 不可用时，把降级原因告诉用户，而不是静默变成「没有进度」。
    """

    def __init__(self, path: Path, *, fallback: ProgressReporter | None = None):
        super().__init__(path, enabled=True)
        self._fallback = fallback
        self._spawned = False

    def start(self, total: float, label: str = "") -> None:
        super().start(total, label)
        if self._spawn_window():
            self._spawned = True
            return
        if self._fallback is not None:
            self._fallback.start(total, label)
            sys.stderr.write(
                "  [进度] 无法打开独立窗口（仅 macOS 支持），已回退为终端内进度。\n"
            )

    def update(self, done: float, note: str = "") -> None:
        super().update(done, note)
        if not self._spawned and self._fallback is not None:
            self._fallback.update(done, note)

    def close(self, summary: str = "") -> None:
        super().close(summary)
        if not self._spawned and self._fallback is not None:
            self._fallback.close(summary)

    def _spawn_window(self) -> bool:
        if not paths.IS_MACOS:
            return False
        script = self.path.with_suffix(".window.sh")
        try:
            script.write_text(
                "#!/bin/bash\n"
                f"cd {shlex.quote(str(paths.PROJECT_ROOT))}\n"
                f"exec {shlex.quote(sys.executable)} -m core.progress "
                f"--watch {shlex.quote(str(self.path))}\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            subprocess.Popen(
                ["open", "-a", "Terminal", str(script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        return True


def make_reporter(mode: str, *, progress_path: Path | None = None,
                  stream=None) -> ProgressReporter:
    """按配置造一个 reporter。

    Args:
        mode: inline | window | off
        progress_path: window 模式必填（JSON 落点）
    """
    inline = InlineReporter(stream)
    if mode == "off":
        return NullReporter()
    if mode == "window" and progress_path is not None:
        return WindowReporter(progress_path, fallback=inline)
    return inline


# ------------------------------------------------------------------ 窗口渲染器
def _watch(json_path: Path) -> int:
    """窗口模式的实际渲染循环：轮询 JSON，画出横向进度条。

    主进程退出（closed）或长时间无心跳（进程被杀）时结束，避免窗口一直挂着。
    """
    last_line = ""
    while True:
        try:
            data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.5)
            continue

        ratio = float(data.get("ratio") or 0.0)
        line = (
            f"\r{render_bar(ratio)} {ratio * 100:5.1f}%  "
            f"{format_duration(data.get('done'))}/{format_duration(data.get('total'))}  "
            f"已用 {format_duration(data.get('elapsed'))}  "
            f"剩 {format_duration(data.get('eta'))}  {data.get('label', '')}  "
            f"{data.get('note', '')}"
        )
        if line != last_line:
            print(line, end="", flush=True)
            last_line = line

        if data.get("closed"):
            summary = data.get("summary") or "完成"
            print(f"\n{summary}\n（进度窗口可关闭）", flush=True)
            return 0

        if time.time() - float(data.get("updated_at") or 0) > _STALE_SEC:
            print("\n主进程似乎已退出，停止刷新。", flush=True)
            return 1

        time.sleep(0.5)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="core.progress", description="进度窗口渲染器")
    ap.add_argument("--watch", type=Path, required=True, help="progress.json 路径")
    args = ap.parse_args(argv)
    return _watch(args.watch)


if __name__ == "__main__":
    raise SystemExit(_main())
