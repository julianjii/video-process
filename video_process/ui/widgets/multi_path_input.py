# -*- coding: utf-8 -*-
"""多文件选择控件：列表 + 添加文件/文件夹 + 原生拖放 + 去重。

与 PathInput 的单路径语义不同，本控件维护一份路径列表，
供 ParamSpec.kind == "files" 的参数使用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import clean_path, human_size, scan_audio, scan_files


class _DropList(QListWidget):
    """把拖放事件转发给 MultiPathInput（列表自身占用 viewport，事件到不了父控件）。"""

    def __init__(self, owner: "MultiPathInput") -> None:
        super().__init__(owner)
        self._owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._owner._handle_drag_enter(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._owner._handle_drag_move(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._owner._handle_drop(event)


class MultiPathInput(QWidget):
    """多文件选择：支持多选文件、添加文件夹（递归扫描音频）与拖放。"""

    #: 路径列表变化
    valueChanged = pyqtSignal(list)
    #: 需要向用户提示一条消息（文本, 级别）
    noticeRequested = pyqtSignal(str, str)

    def __init__(
        self,
        placeholder: str = "尚未添加文件",
        file_patterns: Optional[Iterable[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._paths: List[str] = []
        self._file_patterns: Optional[List[str]] = (
            list(file_patterns) if file_patterns else None
        )
        self._placeholder = placeholder

        self.setAcceptDrops(True)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._list = _DropList(self)
        self._list.setObjectName("multi-file-list")
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._list.setUniformItemSizes(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._list.setMinimumHeight(132)
        layout.addWidget(self._list)

        self._empty_label = QLabel(placeholder, self)
        self._empty_label.setProperty("role", "param-help")
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        add_files = QPushButton("添加文件…", self)
        add_files.setCursor(Qt.CursorShape.PointingHandCursor)
        add_files.clicked.connect(self._pick_files)
        buttons.addWidget(add_files)

        add_dir = QPushButton("添加文件夹…", self)
        add_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        add_dir.clicked.connect(self._pick_directory)
        buttons.addWidget(add_dir)

        remove = QPushButton("移除选中", self)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(remove)

        clear = QPushButton("清空", self)
        clear.setProperty("variant", "ghost")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)

        buttons.addStretch(1)

        self._count_label = QLabel("", self)
        self._count_label.setProperty("role", "param-help")
        buttons.addWidget(self._count_label)

        layout.addLayout(buttons)

        self._refresh()

    # ------------------------------------------------------------------
    # 值
    # ------------------------------------------------------------------
    @property
    def value(self) -> List[str]:
        return list(self._paths)

    def set_value(self, value) -> None:
        if isinstance(value, str):
            items: List[str] = [value]
        else:
            items = list(value or [])

        paths: List[str] = []
        seen = set()
        for raw in items:
            path = clean_path(str(raw or ""))
            if not path:
                continue
            key = self._key(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(os.path.abspath(path))

        self._paths = paths
        self._refresh()
        self.valueChanged.emit(list(self._paths))

    def set_invalid(self, invalid: bool) -> None:
        for widget in (self._list, self._empty_label):
            widget.setProperty("invalid", "true" if invalid else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # ------------------------------------------------------------------
    # 添加 / 移除
    # ------------------------------------------------------------------
    def add_paths(self, candidates: Iterable[str]) -> int:
        """把文件/文件夹加入列表，返回新增文件数。"""
        existing = {self._key(path) for path in self._paths}
        added = 0
        problems: List[str] = []

        for raw in candidates or []:
            path = clean_path(str(raw or ""))
            if not path:
                continue
            if os.path.isdir(path):
                found = self._scan_directory(path)
                if not found:
                    problems.append(f"文件夹内没有匹配的文件: {path}")
                for item in found:
                    if self._append(item, existing):
                        added += 1
            elif os.path.isfile(path):
                if self._append(path, existing):
                    added += 1
            else:
                problems.append(f"路径不存在: {path}")

        if added:
            self._refresh()
            self.valueChanged.emit(list(self._paths))

        if problems:
            text = "；".join(problems[:3])
            if len(problems) > 3:
                text += f"（另有 {len(problems) - 3} 条）"
            self.noticeRequested.emit(text, "warning")
        elif added:
            self.noticeRequested.emit(f"已添加 {added} 个文件", "information")
        return added

    def remove_selected(self) -> None:
        rows = sorted(
            {self._list.row(item) for item in self._list.selectedItems()},
            reverse=True,
        )
        if not rows:
            self.noticeRequested.emit(
                "请先在列表中选择要移除的文件", "information"
            )
            return
        for row in rows:
            if 0 <= row < len(self._paths):
                del self._paths[row]
        self._refresh()
        self.valueChanged.emit(list(self._paths))

    def clear(self) -> None:
        if not self._paths:
            return
        self._paths.clear()
        self._refresh()
        self.valueChanged.emit([])

    # ------------------------------------------------------------------
    # 拖放
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._handle_drag_enter(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._handle_drag_move(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._handle_drop(event)

    def _handle_drag_enter(self, event) -> None:
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def _handle_drag_move(self, event) -> None:
        self._handle_drag_enter(event)

    def _handle_drop(self, event) -> None:
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if paths:
            self.add_paths(paths)

    # ------------------------------------------------------------------
    # 浏览
    # ------------------------------------------------------------------
    def _start_dir(self) -> str:
        if self._paths:
            return os.path.dirname(self._paths[-1]) or str(Path.home())
        return str(Path.home())

    def _pick_files(self) -> None:
        files, _ok = QFileDialog.getOpenFileNames(
            self, "选择音频文件", self._start_dir(), self._build_name_filter()
        )
        if files:
            self.add_paths(files)

    def _pick_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择包含音频的文件夹", self._start_dir(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if directory:
            self.add_paths([directory])

    def _build_name_filter(self) -> str:
        if not self._file_patterns:
            return "所有文件 (*)"
        exts = " ".join(f"*{ext}" for ext in self._file_patterns)
        return f"目标文件 ({exts});;所有文件 (*)"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _scan_directory(self, directory: str) -> List[str]:
        """按 file_patterns 扫描文件夹；未声明时退化为音频。"""
        if self._file_patterns:
            return scan_files(directory, self._file_patterns, recursive=True)
        return scan_audio(directory, recursive=True)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _append(self, path: str, existing: set) -> bool:
        key = self._key(path)
        if key in existing:
            return False
        existing.add(key)
        self._paths.append(os.path.abspath(path))
        return True

    def _refresh(self) -> None:
        self._list.clear()
        total_size = 0
        for path in self._paths:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            total_size += size
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(f"{path}\n{human_size(size)}")
            self._list.addItem(item)

        has_files = bool(self._paths)
        self._list.setVisible(has_files)
        self._empty_label.setVisible(not has_files)
        if has_files:
            self._count_label.setText(
                f"已选 {len(self._paths)} 个 · {human_size(total_size)}"
            )
        else:
            self._count_label.setText("")


__all__ = ["MultiPathInput"]
