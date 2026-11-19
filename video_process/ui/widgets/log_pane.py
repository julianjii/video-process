# -*- coding: utf-8 -*-
"""功能页内的日志区：标题、清空、进度与分级日志。

每个功能页各持一份，后台任务的日志只写回发起任务的功能页，
切换功能不会串台，切回原页面仍保留历史。

为了少占窗口高度，日志默认折叠成一行标题栏（只显示最后一行日志摘要），
任务开始时自动展开；用户也可以点标题栏的箭头或调用 :meth:`toggle` 收起。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.models import LogLevel, ProgressInfo
from .log_view import LogView
from .progress import TaskProgress

#: 展开状态下的最小高度（进度条 + 几行日志）
MIN_EXPANDED_HEIGHT = 110
#: Qt 的「无高度上限」常量（QWIDGETSIZE_MAX）
_NO_MAX_HEIGHT = 16777215
#: 标题行（箭头 / 标题文字 / 摘要）的点击提示
_CLICK_HINT = "点击展开 / 收起日志（任务开始时自动展开）"


class LogPane(QFrame):
    """日志 + 进度组合控件，供功能页与系统日志页复用。"""

    #: 折叠状态变化（供外层分隔条重新分配高度）
    collapsedChanged = pyqtSignal(bool)

    def __init__(
        self,
        title: str = "日志输出",
        max_lines: int = 2000,
        parent: Optional[QWidget] = None,
        collapsed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("log-pane")
        self.setMinimumHeight(MIN_EXPANDED_HEIGHT)

        self._collapsed = False
        self._preview_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._header = header

        self.toggle_button = QPushButton("▾", self)
        self.toggle_button.setObjectName("log-toggle")
        self.toggle_button.setProperty("variant", "ghost")
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setToolTip(_CLICK_HINT)
        self.toggle_button.clicked.connect(self.toggle)
        header.addWidget(self.toggle_button)

        self.header_label = QLabel(title, self)
        self.header_label.setObjectName("log-header-label")
        self._make_clickable(self.header_label)
        header.addWidget(self.header_label)

        # 折叠时在标题行显示最后一行日志，便于判断任务是否还在跑
        self._preview = QLabel("", self)
        self._preview.setProperty("role", "param-help")
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._make_clickable(self._preview)
        header.addWidget(self._preview, 1)

        self.clear_button = QPushButton("清空", self)
        self.clear_button.setProperty("variant", "ghost")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear_log)
        header.addWidget(self.clear_button)

        layout.addLayout(header)

        # 折叠时整块隐藏（含进度条与日志视图）
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)

        self.progress = TaskProgress(self.body)
        body_layout.addWidget(self.progress)

        self.log_view = LogView(max_lines=max_lines, parent=self.body)
        body_layout.addWidget(self.log_view, 1)

        layout.addWidget(self.body, 1)

        self.set_collapsed(collapsed)

    # ------------------------------------------------------------------
    # 折叠 / 展开
    # ------------------------------------------------------------------
    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _make_clickable(self, widget: QWidget) -> None:
        """标题文字与摘要行同样可以点击折叠，不必非点箭头按钮。"""
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setToolTip(_CLICK_HINT)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: D102 - QObject 钩子
        if (
            obj in (self.header_label, self._preview)
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.toggle()
            return True
        return super().eventFilter(obj, event)

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠后只保留标题栏，把高度让给上方的参数区。"""
        collapsed = bool(collapsed)
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self.toggle_button.setText("▸" if collapsed else "▾")

        if collapsed:
            self.setMinimumHeight(0)
            self.setMaximumHeight(self.collapsed_height())
        else:
            self.setMaximumHeight(_NO_MAX_HEIGHT)
            self.setMinimumHeight(MIN_EXPANDED_HEIGHT)

        self._refresh_preview()
        if changed:
            self.collapsedChanged.emit(collapsed)

    def collapsed_height(self) -> int:
        """折叠状态下需要的高度（标题栏 + 上下边距）。"""
        margins = self.layout().contentsMargins()
        hint = self._header.sizeHint().height()
        return int(margins.top() + margins.bottom() + max(hint, 22))

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def log_message(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        self.log_view.write_line(message, level)
        text = (message or "").strip()
        if text:
            self._preview_text = text.splitlines()[0]
            if self._collapsed:
                self._refresh_preview()

    def clear_log(self) -> None:
        self._preview_text = ""
        self.log_view.clear_log()
        self._refresh_preview()

    def flush(self) -> None:
        """立即写入缓冲中的日志（测试或退出前使用）。"""
        self.log_view.flush()

    # ------------------------------------------------------------------
    # 进度
    # ------------------------------------------------------------------
    def begin(self) -> None:
        self.set_collapsed(False)
        self.progress.set_busy()

    def end(self) -> None:
        self.progress.set_idle(True)

    def update_progress(self, info: ProgressInfo) -> None:
        self.progress.update_progress(
            info.clamped(), info.current_file, info.detail
        )

    # ------------------------------------------------------------------
    def _refresh_preview(self) -> None:
        """折叠状态下把最后一行日志摘要显示在标题行。"""
        if not self._collapsed or not self._preview_text:
            self._preview.setText("")
            self._preview.setToolTip(_CLICK_HINT)
            return
        width = max(self._preview.width() - 4, 60)
        shown = self._preview.fontMetrics().elidedText(
            self._preview_text, Qt.TextElideMode.ElideRight, width
        )
        self._preview.setText(shown)
        # 被省略时悬停看全文，否则给出点击提示
        self._preview.setToolTip(
            self._preview_text if shown != self._preview_text else _CLICK_HINT
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D102
        super().resizeEvent(event)
        self._refresh_preview()


__all__ = ["LogPane", "MIN_EXPANDED_HEIGHT"]
