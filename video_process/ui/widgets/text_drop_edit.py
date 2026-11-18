# -*- coding: utf-8 -*-
"""多行文本输入框：支持把本地文本文件直接拖进来。

控件只负责把拖入的文件路径抛给面板，读文件与后续动作由面板决定；
拖动选中文字等非文件拖放仍走 Qt 默认处理。
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

DEFAULT_PLACEHOLDER = "可直接把 .txt 文件拖到这里，或粘贴文本"


class TextDropEdit(QPlainTextEdit):
    """可拖入文件的 QPlainTextEdit。"""

    #: 拖入了一个本地文件（参数为绝对/相对路径）
    fileDropped = pyqtSignal(str)

    def __init__(
        self,
        placeholder: str = DEFAULT_PLACEHOLDER,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText(placeholder)

    # ------------------------------------------------------------------
    # 拖放
    # ------------------------------------------------------------------
    @staticmethod
    def _first_local_file(event) -> str:
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                return path
        return ""

    @staticmethod
    def _accept_file_drag(event) -> bool:
        if not event.mimeData().hasUrls():
            return False
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._accept_file_drag(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not self._accept_file_drag(event):
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._first_local_file(event)
        if not path:
            super().dropEvent(event)
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.fileDropped.emit(path)


__all__ = ["DEFAULT_PLACEHOLDER", "TextDropEdit"]
