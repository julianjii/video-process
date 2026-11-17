# -*- coding: utf-8 -*-
"""ffmpeg 执行器：流式日志、真实进度、可取消、Windows 友好。

相比原项目中散落各处的 subprocess.run(cmd, capture_output=True)，本模块提供：
  - 流式 stderr 日志（逐行回调，长任务不再黑屏干等；同类告警自动折叠限流）
  - 基于 -progress pipe:1 的百分比进度
  - 取消支持（Windows 下用 taskkill 杀进程树，ffmpeg 会派生子进程）
  - 统一 utf-8 编码，避免中文路径乱码
  - Windows 下隐藏控制台窗口闪烁
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Deque, Dict, IO, List, Optional, Sequence

from .models import (
    CancelledError,
    LogLevel,
    ProgressInfo,
    ToolContext,
    ToolError,
)

IS_WINDOWS = sys.platform == "win32"

# 进度节流间隔（秒）——避免高频刷新拖慢终端渲染
_PROGRESS_THROTTLE = 0.2

# 单条命令向 UI 上报的日志行数上限。
# 某些录制源（如直播录像的 mpegts）每十几帧就有一次 PTS 断点，copy 模式下
# ffmpeg 会为每个断点打印一行 "timestamp discontinuity"：实测 2.6 小时 / 1.1 GB
# 的文件在 5 秒内吐出 7.1 万行。逐行转发会让主线程被信号淹没（界面卡死），
# 因此这里做「去重折叠 + 硬上限」双重保险。
_MAX_LOG_LINES = 300

# -stats 进度行（frame=... fps=... time=...）的最小上报间隔（秒）
_STATS_THROTTLE = 1.0

# stderr 原文只用于失败时回显尾部，无需无限累积
_STDERR_TAIL_LINES = 4000

# ffmpeg 输出的噪音行（配置回显等），不值得展示给用户
_NOISE_RE = re.compile(
    r"^\s*(configuration:|lib\w+|built with|\s*$)",
    re.IGNORECASE,
)

# -stats 进度行（每 0.5s 一条，需限频而非去重）
_STATS_RE = re.compile(r"^\s*(?:frame|size)=\s*[\d.]")

# 去重键归一化：内存指针地址、十六进制常量、4 位以上数字
_ADDR_RE = re.compile(r"@\s*[0-9a-fA-F]{6,}|0x[0-9a-fA-F]{4,}")
_BIGNUM_RE = re.compile(r"\d{4,}")


def _fold_key(line: str) -> str:
    """把只差指针地址/数字的行归一化成同一个去重键。

    例：`[aost#0:1/copy @ 000001656cf60d40] Non-monotonic DTS; previous:
    82170780, current: 82162382` 与下一条只差数字，应当折叠成同一类。
    """
    return _BIGNUM_RE.sub("#", _ADDR_RE.sub("@", line)).strip()


#: 判定「时间戳不连续」的阈值（秒），ffmpeg 默认 10。
#:
#: 直播录像类 mpegts 源的 PTS 每几十毫秒就抖动/回退一次，ffmpeg 默认把每次
#: 抖动都当成断点并施加一次补偿偏移，累积后会把视频时间轴整体压缩（实测
#: 2.6 小时录像的视频轨比音频轨早结束 10 分钟，尾部只剩声音、画面定格）。
#:
#: 取 120 的依据（实测同一份 2.6h 录像，裁 5800s 起 1200s，源文件该区间共
#: 21084 个视频帧）：
#:     默认 10  -> 输出 22785 帧（多出 1701 帧，时间轴被压缩，A/V 尾差 560s）
#:     60       -> 输出 21379 帧（64s 大空洞被当成断点，仍有轻微压缩）
#:     120/600  -> 输出 20992 帧（与源基本一致，A/V 尾差 0.2s）
#: 同时用「PTS 回卷 120s」的构造文件验证：阈值 60 仍会补偿该回卷（时长正确
#: 240s），阈值 600 则不再补偿（输出 359s，中间出现 119s 空洞）。故选 120：
#: 既吃掉录制抖动、保持时间轴忠实，又仍能兜住 ≥2 分钟的真实时间戳回卷。
#: 对时间戳正常的文件该选项不触发（实测同一文件加与不加的输出 md5 完全一致）。
_DTS_DELTA_THRESHOLD = 120

#: 后面紧跟 "copy" 表示纯封装的编码参数
_COPY_FLAGS = ("-c", "-codec", "-vcodec", "-acodec",
               "-c:v", "-c:a", "-c:s", "-c:d")


def _inject_ts_options(args: Sequence[str]) -> List[str]:
    """给纯 copy 命令注入 `-dts_delta_threshold`，避免时间戳被错误补偿。

    该选项是 AVFormatContext 级参数，必须出现在受影响的 `-i` 之前。
    重编码命令不做处理：编码器本身会重建时间轴，注入反而可能引入回退。
    """
    cmd = list(args)
    if "-dts_delta_threshold" in cmd or "-i" not in cmd:
        return cmd
    if not os.path.basename(cmd[0]).lower().startswith("ffmpeg"):
        return cmd

    is_copy = any(
        cmd[i] in _COPY_FLAGS and cmd[i + 1] == "copy"
        for i in range(len(cmd) - 1)
    )
    if not is_copy:
        return cmd

    idx = cmd.index("-i")
    return [*cmd[:idx], "-dts_delta_threshold", str(_DTS_DELTA_THRESHOLD), *cmd[idx:]]


@dataclass
class RunResult:
    """命令执行结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _startupinfo():
    """Windows 下隐藏子进程控制台窗口。"""
    if not IS_WINDOWS:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """终止进程树。Windows 下 plain terminate() 杀不掉 ffmpeg 派生的子进程。"""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                startupinfo=_startupinfo(),
                timeout=10,
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _find_executable(name: str) -> Optional[str]:
    return shutil.which(name)


def check_ffmpeg() -> tuple:
    """检测 ffmpeg / ffprobe 是否可用，返回 (ok, ffmpeg_path, ffprobe_path, version_line)。"""
    ffmpeg = _find_executable("ffmpeg")
    ffprobe = _find_executable("ffprobe")
    if not ffmpeg or not ffprobe:
        return False, ffmpeg, ffprobe, ""
    try:
        proc = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_startupinfo(),
            timeout=15,
        )
        first_line = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        return True, ffmpeg, ffprobe, first_line.strip()
    except Exception:
        return False, ffmpeg, ffprobe, ""


class FFmpegRunner:
    """执行 ffmpeg/ffprobe 并流式上报日志与进度。"""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._last_progress_ts = 0.0
        self._stderr_chunks: Deque[str] = collections.deque(
            maxlen=_STDERR_TAIL_LINES
        )

    # ------------------------------------------------------------------
    # 对外主入口
    # ------------------------------------------------------------------
    def run(
        self,
        args: Sequence[str],
        duration: Optional[float] = None,
        description: str = "",
        capture_stderr: bool = True,
        check: bool = True,
    ) -> RunResult:
        """执行一条 ffmpeg/ffprobe 命令。

        参数:
            args:           完整命令行（含程序名）
            duration:       输入媒体总时长（秒），用于把 out_time 换算成百分比
            description:    展示给用户的步骤描述
            capture_stderr: 是否把 stderr 逐行回调到 UI
            check:          返回码非 0 时是否抛出 ToolError
        """
        self.ctx.check_cancel()
        if description:
            self.ctx.log(description)

        # copy 类命令注入时间戳阈值，避免源文件的 PTS 抖动被误补偿
        cmd = _inject_ts_options(args)
        self._stderr_chunks = collections.deque(maxlen=_STDERR_TAIL_LINES)

        popen_args = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_startupinfo(),
        )

        try:
            proc = subprocess.Popen(cmd, **popen_args)
        except FileNotFoundError as exc:
            raise ToolError(f"未找到可执行文件: {cmd[0]}（请确保已安装并加入 PATH）") from exc

        # 读取 stderr 与 stdout 的线程
        stderr_thread = threading.Thread(
            target=self._pump_stream,
            args=(proc.stderr, capture_stderr),
            daemon=True,
        )
        stdout_thread = threading.Thread(
            target=self._pump_progress,
            args=(proc.stdout, duration),
            daemon=True,
        )
        stderr_thread.start()
        stdout_thread.start()

        # 轮询取消
        try:
            while proc.poll() is None:
                if self.ctx.is_cancelled():
                    _kill_process_tree(proc)
                    self.ctx.warn("已发送终止信号，正在取消...")
                    break
                time.sleep(0.1)
            proc.wait()
        finally:
            stderr_thread.join(timeout=5)
            stdout_thread.join(timeout=5)

        if self.ctx.is_cancelled():
            raise CancelledError("任务已被用户取消")

        result = RunResult(
            returncode=proc.returncode or 0,
            stderr="".join(self._stderr_chunks),
        )

        if check and result.returncode != 0:
            raise ToolError(
                f"命令执行失败 (返回码 {result.returncode}): {' '.join(cmd[:6])}...\n"
                + self._tail(result.stderr)
            )
        return result

    # ------------------------------------------------------------------
    # 内部：流泵
    # ------------------------------------------------------------------
    def _pump_stream(self, stream: Optional[IO[str]], report: bool) -> None:
        """把 stderr 逐行转成 UI 日志。

        必须做去重折叠与限频：坏时间戳源文件的 copy 操作会在数秒内刷出数万行
        "timestamp discontinuity"，全量转发会让主线程来不及处理（界面卡死）。
        """
        if stream is None:
            return
        seen: Dict[str, int] = {}
        emitted = 0
        folded = 0
        last_stats_ts = 0.0
        try:
            for line in iter(stream.readline, ""):
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    continue
                self._stderr_chunks.append(line + "\n")
                if not report:
                    continue
                if _NOISE_RE.match(line):
                    continue
                # -stats 进度行：限频显示，保留「跑起来了」的观感
                if _STATS_RE.match(line):
                    now = time.monotonic()
                    if now - last_stats_ts < _STATS_THROTTLE:
                        continue
                    last_stats_ts = now
                    self.ctx.on_log(line, LogLevel.INFO)
                    continue
                # 其余按归一化键去重：同类只留首条，其余计入折叠数
                key = _fold_key(line)
                if key in seen:
                    seen[key] += 1
                    folded += 1
                    continue
                seen[key] = 1
                if emitted >= _MAX_LOG_LINES:
                    folded += 1
                    continue
                emitted += 1
                self.ctx.on_log(line, self._level_of(line))
            if folded:
                self.ctx.warn(f"  （已折叠 {folded} 条重复 / 冗余的 ffmpeg 输出）")
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _level_of(line: str) -> LogLevel:
        """分级着色：ffmpeg 常见关键字。"""
        lowered = line.lower()
        if "error" in lowered or "invalid" in lowered or "failed" in lowered:
            return LogLevel.ERROR
        if "warning" in lowered or "deprecated" in lowered:
            return LogLevel.WARNING
        return LogLevel.INFO

    def _pump_progress(self, stream: Optional[IO[str]], duration: Optional[float]) -> None:
        """解析 ffmpeg -progress 输出的 out_time_ms / out_time 字段。"""
        if stream is None:
            return
        total_us = duration * 1_000_000 if duration and duration > 0 else None
        try:
            for line in iter(stream.readline, ""):
                line = line.strip()
                if not line:
                    continue
                if total_us is None:
                    continue
                # -progress 输出为 key=value 形式
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        raw = line.split("=", 1)[1].strip()
                        if raw in ("N/A", ""):
                            continue
                        us = int(raw)
                        if line.startswith("out_time_ms="):
                            us *= 1000
                        percent = (us / total_us) * 100.0
                        self._emit_progress(percent)
                    except (ValueError, ZeroDivisionError):
                        continue
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _emit_progress(self, percent: float) -> None:
        """节流上报，避免每帧都刷新 UI。"""
        now = time.monotonic()
        if now - self._last_progress_ts < _PROGRESS_THROTTLE:
            return
        self._last_progress_ts = now
        self.ctx.on_progress(ProgressInfo(percent=max(0.0, min(100.0, percent))))

    @staticmethod
    def _tail(text: str, limit: int = 25) -> str:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) <= limit:
            return "\n".join(lines)
        return "\n".join(lines[-limit:])


def build_progress_args() -> List[str]:
    """返回启用机器可读进度输出的参数（需插在输出文件之前）。"""
    return ["-progress", "pipe:1", "-nostats"]


#: 支持 -movflags +faststart 的容器（MP4 系）
_FASTSTART_EXTS = (".mp4", ".mov", ".m4v")


def build_faststart_args(output: str) -> List[str]:
    """把 moov 前置到文件头，仅对 MP4 系容器生效。

    除了让播放器/网页秒开，还绕开一个实测问题：纯 copy 产出接近 1 GB 的 MP4
    时，写在文件尾的 moov 会少最后 1 KB，整份文件因 "moov atom not found"
    打不开（同样参数加 faststart 后文件正常）。
    """
    if os.path.splitext(output)[1].lower() in _FASTSTART_EXTS:
        return ["-movflags", "+faststart"]
    return []


def run_quiet(args: Sequence[str]) -> RunResult:
    """无 UI 上下文的静默执行（ffprobe 探测等短命令用）。"""
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_startupinfo(),
            timeout=60,
        )
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    except FileNotFoundError as exc:
        raise ToolError(f"未找到可执行文件: {args[0]}") from exc
    except subprocess.TimeoutExpired:
        return RunResult(-1, "", "命令执行超时")
