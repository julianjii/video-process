# -*- coding: utf-8 -*-
"""功能页与系统日志页。

ToolPage 是日志归属单元：上方工具面板、下方本页日志，中间可拖动。
它同时实现 PanelHost，因此面板里的 ``host.log_message(...)`` 会自动
落到本页日志，后台任务切走后再写日志也不会串到别的功能。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.models import LogLevel, ProgressInfo, TaskResult
from ..param_spec import ToolDefinition
from .host import WindowHost
from .panels import create_panel
from .panels.base import ToolPanel
from .widgets import LogPane, LogView


class ToolPage(QWidget):
    """一个功能的完整页面：参数面板 + 独立日志区。"""

    def __init__(
        self,
        definition: ToolDefinition,
        window: WindowHost,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.window = window

        # 日志默认折叠成标题栏，任务开始时由 LogPane.begin() 自动展开
        self._log = LogPane(title=f"{definition.title} · 日志", parent=self)
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setHandleWidth(1)
        self._splitter.setChildrenCollapsible(False)

        self._panel = self._create_panel()
        self._splitter.addWidget(self._panel)
        self._splitter.addWidget(self._log)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([640, 40])
        self._log.collapsedChanged.connect(self._on_log_collapsed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._splitter, 1)

    # ==================================================================
    # 面板 / 日志高度分配
    # ==================================================================
    def _on_log_collapsed(self, collapsed: bool) -> None:
        """日志展开时给它一个合理高度，折叠时把高度还给参数区。"""
        total = max(self._splitter.height(), 480)
        if collapsed:
            log_height = self._log.collapsed_height()
        else:
            log_height = min(240, max(160, int(total * 0.35)))
        self._splitter.setSizes([total - log_height, log_height])

    def _create_panel(self) -> ToolPanel:
        return create_panel(self.definition, self, None)

    @property
    def panel(self) -> ToolPanel:
        return self._panel

    @property
    def log_view(self) -> LogView:
        return self._log.log_view

    @property
    def running(self) -> bool:
        return self._panel.running

    def rebuild_panel(self) -> bool:
        """按当前设置重建参数面板，保留本页日志。

        正在执行任务时跳过，避免销毁仍有回调的面板。
        """
        if self._panel.running:
            return False
        old = self._panel
        new = self._create_panel()
        self._splitter.replaceWidget(0, new)
        self._panel = new
        old.deleteLater()
        return True

    # ==================================================================
    # PanelHost：日志与进度（落到本页）
    # ==================================================================
    @property
    def settings(self):
        return self.window.settings

    def log_message(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        self._log.log_message(message, level)

    def log_system(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        self.window.log_system(message, level)

    def update_progress(self, info: ProgressInfo) -> None:
        self._log.update_progress(info)

    def add_link(self, link: str) -> None:
        """后台任务实时上报的链接，交给面板立即显示。"""
        self._panel.add_link(link)

    def clear_log(self) -> None:
        self._log.clear_log()

    def flush_log(self) -> None:
        self._log.flush()

    # ==================================================================
    # PanelHost：任务生命周期
    # ==================================================================
    def begin_task(self, title: str) -> bool:
        if not self.window.reserve_task(self):
            return False
        self.log_message("")
        self.log_message(f"═══ 开始执行：{title} ═══", LogLevel.SUCCESS)
        self._log.begin()
        return True

    def end_task(self, result: Optional[TaskResult] = None) -> None:
        self._log.end()
        if result is not None:
            self._log_result(result)
        self.window.release_task(self)

    def _log_result(self, result: TaskResult) -> None:
        cancelled = "取消" in (result.message or "")
        if result.success:
            self.log_message(f"✔ {result.message}", LogLevel.SUCCESS)
            self.window.notify(result.message, "information", timeout=6)
        elif cancelled:
            self.log_message(f"■ {result.message}", LogLevel.WARNING)
            self.window.notify(result.message, "warning", timeout=5)
        else:
            self.log_message(f"✖ {result.message}", LogLevel.ERROR)
            self.window.notify(result.message, "error", timeout=10)

    def finish_task(self, result: TaskResult) -> None:
        """任务结束：写结果并复位，再把结果交给面板展示。"""
        self.end_task(result)
        self._panel.on_result(result)

    def run_task(
        self, definition: ToolDefinition, values: dict,
        panel: object = None,
    ) -> bool:
        if not self.begin_task(definition.title):
            return False
        if not self.window.start_task(self, definition, values):
            self.end_task(None)
            return False
        return True

    def cancel_task(self) -> None:
        self.window.cancel_task()

    def is_task_cancelled(self) -> bool:
        return self.window.is_task_cancelled()

    def notify(
        self, message: str, severity: str = "information", timeout: int = 5
    ) -> None:
        self.window.notify(message, severity, timeout)

    def fill_remove_segments(
        self, csv: str = "", input_path: str = "", navigate: bool = True
    ) -> None:
        self.window.fill_remove_segments(csv, input_path, navigate)

    def reload_panels(self, keep: object = None) -> None:
        self.window.reload_defaults(
            keep=self if keep is self._panel else None
        )


class SystemLogPage(QWidget):
    """系统级消息页：ffmpeg/AMF 自检、设置保存与任务调度。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(0)

        title = QLabel("系统日志", self)
        title.setProperty("role", "panel-title")
        layout.addWidget(title)

        desc = QLabel(
            "ffmpeg / AMF 检测、设置保存与任务调度；各功能执行日志见功能页底部",
            self,
        )
        desc.setProperty("role", "panel-desc")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(4)

        # 本页就是日志页，默认展开
        self._log = LogPane(title="系统消息", parent=self, collapsed=False)
        layout.addWidget(self._log, 1)

    # ------------------------------------------------------------------
    def log_message(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        self._log.log_message(message, level)

    def clear_log(self) -> None:
        self._log.clear_log()

    def flush_log(self) -> None:
        self._log.flush()

    @property
    def log_view(self) -> LogView:
        return self._log.log_view


__all__ = ["ToolPage", "SystemLogPage"]
