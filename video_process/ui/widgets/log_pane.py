# -*- coding: utf-8 -*-
"""功能页内的日志区：标题、清空、进度与分级日志。

每个功能页各持一份，后台任务的日志只写回发起任务的功能页，
切换功能不会串台，切回原页面仍保留历史。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import LogLevel, ProgressInfo
from .log_view import LogView
from .progress import TaskProgress


class LogPane(QFrame):
    """日志 + 进度组合控件，供功能页与系统日志页复用。"""

    def __init__(
        self,
        title: str = "日志输出",
        max_lines: int = 2000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("log-pane")
        self.setMinimumHeight(110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.header_label = QLabel(title, self)
        self.header_label.setObjectName("log-header-label")
        header.addWidget(self.header_label)
        header.addStretch(1)

        self.clear_button = QPushButton("清空", self)
        self.clear_button.setProperty("variant", "ghost")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear_log)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.progress = TaskProgress(self)
        layout.addWidget(self.progress)

        self.log_view = LogView(max_lines=max_lines, parent=self)
        layout.addWidget(self.log_view, 1)

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def log_message(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        self.log_view.write_line(message, level)

    def clear_log(self) -> None:
        self.log_view.clear_log()

    def flush(self) -> None:
        """立即写入缓冲中的日志（测试或退出前使用）。"""
        self.log_view.flush()

    # ------------------------------------------------------------------
    # 进度
    # ------------------------------------------------------------------
    def begin(self) -> None:
        self.progress.set_busy()

    def end(self) -> None:
        self.progress.set_idle(True)

    def update_progress(self, info: ProgressInfo) -> None:
        self.progress.update_progress(
            info.clamped(), info.current_file, info.detail
        )
