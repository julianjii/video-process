# -*- coding: utf-8 -*-
"""主窗口与单功能窗口共用的外壳：标题栏、状态栏与环境自检。"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from .. import __version__
from .._frozen import bundled_ffmpeg_dir
from ..core.ffmpeg import check_ffmpeg
from ..core.models import LogLevel
from ..core.probe import check_amf_support

#: 状态栏右侧快捷键提示（两个窗口共用）
HINT = "Ctrl+Q 退出 · Ctrl+C 停止任务 · Ctrl+L 清空当前日志 · F1 快捷键"


def ffmpeg_source() -> str:
    """当前使用的 ffmpeg 来源：内置（打包）或系统 PATH。"""
    return "内置" if bundled_ffmpeg_dir() else "系统"


def short_ffmpeg_version(version: str) -> str:
    return version.split(" Copyright")[0] if version else "未知版本"


def env_log_lines(ok: bool, version: str) -> List[Tuple[str, LogLevel]]:
    """把环境自检结果整理成要写日志的行。"""
    if ok:
        return [
            (
                f"ffmpeg 检测通过（{ffmpeg_source()}）: "
                f"{short_ffmpeg_version(version)}",
                LogLevel.SUCCESS,
            )
        ]
    return [
        ("未检测到 ffmpeg/ffprobe，请先安装并加入 PATH", LogLevel.ERROR)
    ]


class EnvChecker(QThread):
    """环境自检：spawn 子进程查询 ffmpeg 与 AMF，放后台线程避免卡启动。"""

    finished = pyqtSignal(bool, str, list)

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            ok, _ffmpeg, _ffprobe, version = check_ffmpeg()
        except Exception:
            ok, version = False, ""
        try:
            amf = check_amf_support()
        except Exception:
            amf = []
        self.finished.emit(bool(ok), str(version or ""), list(amf or []))


class EnvChips(QWidget):
    """标题栏右侧的 ffmpeg / AMF 状态芯片。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.ffmpeg = QLabel("检测中…", self)
        self.ffmpeg.setProperty("chip", "env")
        self.ffmpeg.setProperty("state", "")
        layout.addWidget(self.ffmpeg)

        self.amf = QLabel("AMF: 检测中…", self)
        self.amf.setProperty("chip", "env")
        self.amf.setProperty("state", "")
        layout.addWidget(self.amf)

    def render(self, ok: bool, version: str, amf: List[str]) -> None:
        """更新芯片文案与状态色。"""
        if ok:
            self._set(
                self.ffmpeg,
                f"● ffmpeg（{ffmpeg_source()}）: "
                f"{short_ffmpeg_version(version)}",
                "ok",
            )
        else:
            self._set(self.ffmpeg, "● 未检测到 ffmpeg / ffprobe", "error")

        if amf:
            self._set(self.amf, f"● AMF: {', '.join(amf)}", "ok")
        else:
            self._set(
                self.amf, "● AMF: 不可用（AMD 相关模式将失败）", "warn"
            )

    @staticmethod
    def _set(label: QLabel, text: str, state: str) -> None:
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.adjustSize()


class TitleBar(QFrame):
    """应用标题栏：名称 + 版本 + 环境芯片。"""

    def __init__(
        self, title: str, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("title-bar")
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("app-title")
        layout.addWidget(self.title_label)

        version = QLabel(f"v{__version__}", self)
        version.setObjectName("app-version")
        layout.addWidget(version)

        layout.addStretch(1)

        self.env = EnvChips(self)
        layout.addWidget(self.env)


class StatusBar(QFrame):
    """状态栏：左侧任务状态 / 临时提示，右侧快捷键说明。"""

    def __init__(
        self, hint: str, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("status-bar")

        self._running = False
        self._title = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self.state = QLabel("空闲", self)
        layout.addWidget(self.state)
        layout.addStretch(1)

        hint_label = QLabel(hint, self)
        hint_label.setProperty("role", "param-help")
        layout.addWidget(hint_label)

        # 提示的自动复位由本控件持有，控件销毁时定时器一并销毁
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reset)

    # ------------------------------------------------------------------
    @classmethod
    def attach(cls, window: QMainWindow, hint: str) -> "StatusBar":
        """把状态栏挂到窗口底部。"""
        bar = cls(hint, window)
        status = window.statusBar()
        status.setSizeGripEnabled(False)
        status.setFixedHeight(28)
        status.addWidget(bar, 1)
        return bar

    def set_running(self, running: bool, title: str = "") -> None:
        self._running = running
        self._title = title if running else ""
        self._timer.stop()
        self._reset()

    def notify(
        self, message: str, severity: str = "information", timeout: int = 5
    ) -> None:
        lines = (message or "").splitlines()
        first = lines[0] if lines else ""
        if severity == "error":
            self.state.setText(f"✖ {first}")
        elif severity == "warning":
            self.state.setText(f"! {first}")
        else:
            self.state.setText(first)
        self._timer.start(max(1, timeout) * 1000)

    def _reset(self) -> None:
        if self._running:
            self.state.setText(f"运行中：{self._title}")
        else:
            self.state.setText("空闲")


__all__ = [
    "HINT",
    "EnvChecker",
    "EnvChips",
    "StatusBar",
    "TitleBar",
    "env_log_lines",
    "ffmpeg_source",
    "short_ffmpeg_version",
]
