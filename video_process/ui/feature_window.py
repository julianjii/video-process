# -*- coding: utf-8 -*-
"""单功能独立窗口：只承载一个功能页（参数 + 本页日志）。

启动方式: ``python main.py <功能名>``，例如 ``python main.py merge``。
窗口结构与主窗口一致（标题栏 + 状态栏 + 环境芯片），只是没有左侧导航。
"""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.models import LogLevel, Settings
from ..defs import TOOL_DEFS
from ..param_spec import ToolDefinition
from ..settings_store import load_settings
from .task_hub import TaskHub
from .tool_page import ToolPage
from .window_shell import (
    HINT,
    EnvChecker,
    StatusBar,
    TitleBar,
    env_log_lines,
)


class FeatureWindow(QMainWindow):
    """单功能窗口，同时充当该功能页的 WindowHost。"""

    def __init__(self, tool_id: str) -> None:
        super().__init__()
        definition = TOOL_DEFS.get(tool_id)
        if definition is None:
            raise ValueError(f"未知功能: {tool_id}")

        self.settings: Settings = load_settings()
        self.definition: ToolDefinition = definition
        self.hub = TaskHub(self.log_system, self)

        self.setWindowTitle(
            f"{definition.title} · 视频处理工具集 v{__version__}"
        )
        self.resize(1000, 780)
        self.setMinimumSize(760, 560)

        self._build_ui()
        self.status_bar = StatusBar.attach(self, HINT)
        self._build_shortcuts()

        QTimer.singleShot(60, self._check_environment)

    # ==================================================================
    # 布局
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self.definition.title, central)
        root.addWidget(self.title_bar)

        self.page = ToolPage(self.definition, self, central)
        root.addWidget(self.page, 1)

        self.setCentralWidget(central)

    # ==================================================================
    # 快捷键
    # ==================================================================
    def _build_shortcuts(self) -> None:
        self._add_shortcut("Ctrl+Q", self.close)
        self._add_shortcut("Ctrl+C", self.cancel_task)
        self._add_shortcut("Ctrl+L", self.page.clear_log)
        self._add_shortcut("F1", self.action_toggle_help)

    def _add_shortcut(self, sequence: str, slot) -> QAction:
        action = QAction(self)
        action.setShortcut(QKeySequence(sequence))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def action_toggle_help(self) -> None:
        QMessageBox.information(
            self,
            "快捷键",
            "Ctrl+Q  退出\n"
            "Ctrl+C  停止任务\n"
            "Ctrl+L  清空本功能日志\n"
            "F1      显示本帮助\n\n"
            "参数：Tab 在控件间移动，路径可直接拖入\n"
            "日志：参数区与日志区之间的分隔条可拖动",
        )

    # ==================================================================
    # 环境自检
    # ==================================================================
    def _check_environment(self) -> None:
        self._env_checker = EnvChecker(self)
        self._env_checker.finished.connect(self._render_env)
        self._env_checker.start()

    def _render_env(
        self, ok: bool, version: str, amf: List[str]
    ) -> None:
        self.title_bar.env.render(ok, version, amf)
        for line, level in env_log_lines(ok, version):
            self.page.log_message(line, level)
        if not ok:
            self.notify(
                "未检测到 ffmpeg/ffprobe，本功能将不可用", "error", timeout=10
            )

    # ==================================================================
    # WindowHost：通知与系统日志
    # ==================================================================
    def notify(
        self, message: str, severity: str = "information", timeout: int = 5
    ) -> None:
        self.status_bar.notify(message, severity, timeout)

    def log_system(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        page = getattr(self, "page", None)
        if page is not None:
            page.log_message(f"[系统] {message}", level)

    # ==================================================================
    # WindowHost：任务调度
    # ==================================================================
    def reserve_task(self, page: object) -> bool:
        if not self.hub.reserve(page):
            self.notify("已有任务正在运行", "warning", timeout=3)
            self.log_system("已有任务正在运行，已拒绝新的执行请求",
                            LogLevel.WARNING)
            return False
        title = getattr(getattr(page, "definition", None), "title", "")
        self.status_bar.set_running(True, title)
        return True

    def release_task(self, page: object) -> None:
        self.hub.release(page)
        if not self.hub.running:
            self.status_bar.set_running(False)

    def start_task(
        self, page: object, definition: ToolDefinition, values: dict
    ) -> bool:
        return self.hub.start(page, definition, values, self.settings)

    def cancel_task(self) -> None:
        if not self.hub.running:
            self.notify("当前没有正在运行的任务", "information", timeout=2)
            return
        self.hub.cancel()
        self.log_system(
            "正在请求停止…（等待当前 ffmpeg 进程退出）", LogLevel.WARNING
        )

    def is_task_cancelled(self) -> bool:
        return self.hub.is_cancelled()

    # ==================================================================
    # WindowHost：跨功能回填 / 设置刷新
    # ==================================================================
    def fill_remove_segments(
        self, csv: str = "", input_path: str = "", navigate: bool = True
    ) -> None:
        """单窗口没有「片段删除」页：本身是它时填本页，否则复制到剪贴板。"""
        del navigate  # 单窗口没有导航可切换

        if self.definition.id == "remove-segments":
            panel = self.page.panel
            parts: List[str] = []
            if csv and hasattr(panel, "fill_value") and panel.fill_value(
                "segments", csv
            ):
                parts.append(f"时间段 {csv}")
            if input_path and hasattr(panel, "fill_value") and panel.fill_value(
                "input_path", input_path
            ):
                parts.append(f"视频 {input_path}")
            if parts:
                message = "已填入本页：" + "；".join(parts)
                self.page.log_message(message, LogLevel.SUCCESS)
                self.notify(message, "information", timeout=5)
            elif csv or input_path:
                self.notify("填入失败：未找到对应字段", "error", timeout=5)
            return

        if not csv:
            self.notify(
                "独立窗口没有「片段删除」页，请打开完整界面后手动填写",
                "warning",
                timeout=6,
            )
            return

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(csv)
        self.page.log_message(
            f"广告区间 CSV 已复制到剪贴板: {csv}", LogLevel.SUCCESS
        )
        self.notify(
            "独立窗口无「片段删除」页，广告区间已复制到剪贴板",
            "information",
            timeout=6,
        )

    def reload_defaults(self, keep: object = None) -> None:
        if keep is self.page:
            return
        self.page.rebuild_panel()

    # ==================================================================
    # 关闭保护
    # ==================================================================
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hub.running:
            answer = QMessageBox.question(
                self,
                "任务正在运行",
                "当前任务尚未结尾，关闭窗口会中断它。确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.hub.cancel()
            self.hub.wait(3000)
        event.accept()


def create_feature_window(tool_id: str) -> FeatureWindow:
    """创建单功能窗口（不进入事件循环，便于测试驱动）。"""
    return FeatureWindow(tool_id)


__all__ = ["FeatureWindow", "create_feature_window"]
