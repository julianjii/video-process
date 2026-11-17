# -*- coding: utf-8 -*-
"""命令行入口：把 defs/ 里的每个工具原样暴露为一个子命令。

参数规格直接取自 defs（与界面共用同一份声明），因此命令行的参数名、
默认值、取值范围、校验规则永远与图形界面一致——本模块不复制任何业务
逻辑，也不 import 任何 Qt 模块（否则打包后的控制台版会拖进整个 Qt）。

用法:
    python -m video_process.cli merge D:\\videos
    python -m video_process.cli remove-segments a.mp4 --segments 开头-1:00,5:00-结尾
    python -m video_process.cli timeline --text - < 时间线.txt

约定:
    · 日志、进度、结果摘要走 stderr；产物路径或纯文本结果逐行写 stdout，
      因此可以直接 `> out.txt` 或用管道接着处理
    · 退出码 0 成功 / 1 失败 / 2 参数错误
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import traceback
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from ._frozen import prepare_ffmpeg_path
from ._io import ensure_utf8_stdio
from .core.models import (
    CancelledError,
    LogLevel,
    ProgressInfo,
    Settings,
    TaskResult,
    ToolContext,
    ToolError,
)
from . import defs
from .param_spec import ParamSpec, ToolDefinition
from .settings_store import CONFIG_PATH, load_settings

#: 不提供子命令的工具（纯界面面板，没有业务函数）
SKIP_IDS = {"settings"}

#: 进度行宽度（用于擦除上一次输出）
_PROGRESS_WIDTH = 28


# ---------------------------------------------------------------------------
# 参数规格 -> argparse
# ---------------------------------------------------------------------------
def _default_for(spec: ParamSpec, settings: Settings) -> Any:
    """默认值：全局设置优先，设置值为 None 时保留规格自身的默认值。

    与界面（ui/panels/base.py 的 _apply_setting）同一套规则，
    这样「留空=自动」之类的语义不会被设置里的空值顶掉。
    """
    if spec.setting:
        value = getattr(settings, spec.setting, None)
        if value is not None:
            return value
    return spec.default


def _help_for(spec: ParamSpec, default: Any) -> str:
    text = spec.help or spec.label
    if spec.choices:
        options = "，".join(f"{value}={label}" for label, value in spec.choices)
        text += f"（可选: {options}）"
    if spec.kind == "bool":
        text += "（默认开启）" if default else "（默认关闭）"
    elif default not in (None, ""):
        text += f"（默认: {default}）"
    return text


def _add_param(parser: argparse.ArgumentParser, spec: ParamSpec,
               settings: Settings) -> None:
    """把一条 ParamSpec 加成一个命令行参数。"""
    default = _default_for(spec, settings)
    flag = "--" + spec.name.replace("_", "-")
    kwargs: Dict[str, Any] = {"help": _help_for(spec, default), "default": default}

    # 多行文本不设必填：省略时可以从标准输入读（见 _collect_values）
    if spec.required and spec.default in (None, "") and not spec.multiline:
        kwargs["required"] = True

    if spec.kind == "bool":
        parser.add_argument(
            flag, action=argparse.BooleanOptionalAction, default=bool(default),
            help=kwargs["help"],
        )
        return

    if spec.kind == "choice":
        values = [value for _label, value in (spec.choices or [])]
        kwargs["choices"] = values
        # merge / convert 的 mode 存的是整数 1/2/3，命令行也要按整数解析
        if values and all(isinstance(value, int) for value in values):
            kwargs["type"] = int
    elif spec.kind == "int":
        kwargs["type"] = int
    elif spec.kind in ("float", "stepper"):
        kwargs["type"] = float

    parser.add_argument(flag, **kwargs)


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="只显示警告与错误",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="本次允许覆盖已存在的输出（不改写配置文件）",
    )


def build_parser(
    settings: Settings,
) -> Tuple[argparse.ArgumentParser, Dict[str, Tuple[ToolDefinition, argparse.ArgumentParser]]]:
    """按 defs 生成全部子命令。

    返回:
        (顶层解析器, {功能名: (定义, 该子命令的解析器)})
    """
    parser = argparse.ArgumentParser(
        prog="video-process-cli",
        description=(
            "视频处理工具集 · 命令行版\n"
            "参数与图形界面完全一致；每个功能加 --help 查看参数、默认值与取值范围。\n"
            f"全局设置文件: {CONFIG_PATH}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"video-process {__version__}"
    )

    sub = parser.add_subparsers(dest="tool", metavar="功能")
    tools: Dict[str, Tuple[ToolDefinition, argparse.ArgumentParser]] = {}

    for group, ids in defs.NAV_GROUPS:
        for tool_id in ids:
            definition = defs.TOOL_DEFS[tool_id]
            if tool_id in SKIP_IDS or definition.resolve_runner() is None:
                continue
            child = sub.add_parser(
                tool_id,
                help=f"[{group}] {definition.title}",
                description=f"[{group}] {definition.title}\n\n{definition.description}",
                formatter_class=argparse.RawDescriptionHelpFormatter,
            )
            for spec in definition.specs:
                _add_param(child, spec, settings)
            _add_global_flags(child)
            tools[tool_id] = (definition, child)

    return parser, tools


# ---------------------------------------------------------------------------
# 取值与校验
# ---------------------------------------------------------------------------
def _configure_stdin() -> None:
    """把标准输入切到 UTF-8。

    Windows 下管道默认按本地代码页（GBK）解码，中文时间线/文本经 `-`
    或管道送进来会变成乱码，这里与 ensure_utf8_stdio() 对齐。
    """
    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return ""


def _stdin_has_data() -> bool:
    """标准输入是否被管道/文件重定向（tty 下不读，避免命令行挂起等输入）。"""
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def collect_values(definition: ToolDefinition, args: argparse.Namespace) -> Dict[str, Any]:
    """把解析结果整理成业务函数需要的 values 字典。"""
    values: Dict[str, Any] = {}
    for spec in definition.specs:
        raw = getattr(args, spec.name)
        if isinstance(raw, str) and spec.kind == "text":
            if raw.strip() == "-":
                raw = _read_stdin()
            elif spec.multiline and not raw and _stdin_has_data():
                raw = _read_stdin()
        values[spec.name] = spec.coerce(raw)
    return values


def validate(definition: ToolDefinition, values: Dict[str, Any]) -> List[str]:
    """跑一遍规格里声明的校验函数（与界面同源），返回错误信息列表。"""
    errors: List[str] = []
    for spec in definition.specs:
        if spec.validator is None or not spec.should_show(values):
            continue
        message = spec.validator(values[spec.name])
        if message:
            errors.append(f"--{spec.name.replace('_', '-')}: {message}")
    return errors


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _log_to_stderr(message: str, level: LogLevel) -> None:
    prefix = ""
    if level is LogLevel.WARNING:
        prefix = "警告: "
    elif level is LogLevel.ERROR:
        prefix = "错误: "
    print(prefix + str(message), file=sys.stderr, flush=True)


def _install_sigint(cancel: threading.Event) -> Any:
    """Ctrl+C 只置取消标志，由 FFmpegRunner 的轮询去杀进程树。

    返回原处理器以便恢复（同进程内反复调用 main 时不会互相干扰）。
    """
    def handler(_signum, _frame):
        if not cancel.is_set():
            print("\n正在取消，等待 ffmpeg 退出...", file=sys.stderr, flush=True)
        cancel.set()

    try:
        return signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):  # 非主线程 / 无信号支持时忽略
        return None


def _restore_sigint(previous: Any) -> None:
    if previous is None:
        return
    try:
        signal.signal(signal.SIGINT, previous)
    except (ValueError, OSError):
        pass


def run_tool(definition: ToolDefinition, values: Dict[str, Any],
             settings: Settings, quiet: bool) -> int:
    """执行一个工具，返回进程退出码。"""
    cancel = threading.Event()
    state = {"progress": False}

    def erase_progress() -> None:
        if state["progress"]:
            sys.stderr.write("\r" + " " * _PROGRESS_WIDTH + "\r")
            sys.stderr.flush()
            state["progress"] = False

    def on_log(message: str, level: LogLevel) -> None:
        if quiet and level not in (LogLevel.WARNING, LogLevel.ERROR):
            return
        erase_progress()
        _log_to_stderr(message, level)

    def on_progress(info: ProgressInfo) -> None:
        percent = info.clamped()
        if quiet or percent is None or not _stderr_is_tty():
            return
        sys.stderr.write(f"\r  进度 {percent:5.1f}%")
        sys.stderr.flush()
        state["progress"] = True

    ctx = ToolContext(
        on_log=on_log,
        on_progress=on_progress,
        is_cancelled=cancel.is_set,
        settings=settings,
    )

    runner = definition.resolve_runner()
    if runner is None:
        print(f"工具 {definition.id} 尚未接入业务逻辑", file=sys.stderr)
        return 1

    previous = _install_sigint(cancel)
    try:
        result = runner(ctx=ctx, **values)
    except CancelledError as exc:
        result = TaskResult(success=False, message=str(exc))
    except ToolError as exc:
        result = TaskResult(success=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - 兜底，与界面一致地转成失败结果
        traceback.print_exc()
        result = TaskResult(success=False, message=f"执行异常: {exc}")
    finally:
        _restore_sigint(previous)
        erase_progress()

    for warning in result.warnings:
        print(f"提示: {warning}", file=sys.stderr, flush=True)
    if result.message and not quiet:
        print(result.message, file=sys.stderr, flush=True)
    for item in result.outputs:
        print(item, flush=True)

    return 0 if result.success else 1


def _stderr_is_tty() -> bool:
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口，返回进程退出码。"""
    ensure_utf8_stdio()
    _configure_stdin()
    prepare_ffmpeg_path()

    settings = load_settings()
    parser, tools = build_parser(settings)
    args = parser.parse_args(argv)

    if not args.tool:
        parser.print_help()
        return 0

    definition, sub_parser = tools[args.tool]
    values = collect_values(definition, args)

    errors = validate(definition, values)
    if errors:
        sub_parser.error("；".join(errors))

    if getattr(args, "overwrite", False):
        settings.overwrite = True

    return run_tool(definition, values, settings, quiet=getattr(args, "quiet", False))


if __name__ == "__main__":
    raise SystemExit(main())
