# -*- coding: utf-8 -*-
"""音频批量转字幕（远程 API）面板。

通用表单负责服务地址、音频列表与语种；本面板追加一块只读区域，
汇总本次所有任务的查询地址，并提供一键复制。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import LogLevel, TaskResult
from ...param_spec import ToolDefinition
from ..host import PanelHost
from .base import ToolPanel


class RemoteSubtitlePanel(ToolPanel):
    """远程转字幕：查询地址结果区 + 复制按钮。"""

    def __init__(
        self,
        definition: ToolDefinition,
        host: PanelHost,
        parent: Optional[QWidget] = None,
    ) -> None:
        self._links_view: Optional[QPlainTextEdit] = None
        super().__init__(definition, host, parent)

    # ==================================================================
    # 结果区
    # ==================================================================
    def build_extra(self, content: QWidget, layout: QVBoxLayout) -> None:
        section = QLabel("任务查询地址（结果保留 1 小时）", content)
        section.setProperty("role", "section")
        layout.addWidget(section)

        self._links_view = QPlainTextEdit(content)
        self._links_view.setReadOnly(True)
        self._links_view.setFixedHeight(140)
        self._links_view.setPlaceholderText(
            "上传成功后，这里会列出每个音频对应的查询地址（文件名 -> URL）…"
        )
        layout.addWidget(self._links_view)

    def build_footer(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(8)

        copy_button = QPushButton("复制查询地址", self)
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(self._copy_links)
        row.addWidget(copy_button)

        row.addStretch(1)
        layout.addLayout(row)

    # ==================================================================
    # 执行摘要 / 结果
    # ==================================================================
    def build_summary(self, values: Dict[str, Any]) -> List[str]:
        base = str(values.get("base_url") or "").strip() or "(未填写)"
        language = str(values.get("language") or "Chinese")
        files = list(values.get("files") or [])

        lines = [
            f"服务地址: {base}",
            f"音频语种: {language}",
            f"音频/视频文件: {len(files)} 个",
        ]
        for path in files[:5]:
            lines.append(f"  · {path}")
        if len(files) > 5:
            lines.append(f"  … 其余 {len(files) - 5} 个")
        lines.append("")
        lines.append("转换成功后字幕保存到各音频所在文件夹（同名 .srt 直接覆盖）。")
        lines.append("视频会先提取音轨为 MP3（保存到视频同目录）再上传。")
        lines.append("服务端最多并行 5 个任务，程序会自动排队与轮询。")
        return lines

    def start_task(self, values: Dict[str, Any]) -> None:
        if self._links_view is not None:
            self._links_view.clear()
        super().start_task(values)

    def on_result(self, result: TaskResult) -> None:
        links = list(getattr(result, "links", []) or [])
        if links and self._links_view is not None:
            self._links_view.setPlainText("\n".join(links))
            self.host.log_message(
                f"本次共 {len(links)} 个任务查询地址已列在面板中",
                LogLevel.INFO,
            )
        super().on_result(result)

    def reset_params(self) -> None:
        super().reset_params()
        if self._links_view is not None:
            self._links_view.clear()

    # ==================================================================
    # 复制
    # ==================================================================
    def _copy_links(self) -> None:
        text = (
            self._links_view.toPlainText().strip()
            if self._links_view is not None
            else ""
        )
        if not text:
            self.host.notify("暂无查询地址", "warning", timeout=3)
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            self.host.notify("复制失败，请手动选择文本复制", "warning", timeout=4)
            return
        clipboard.setText(text)
        self.host.notify("查询地址已复制到剪贴板", "information", timeout=3)


__all__ = ["RemoteSubtitlePanel"]
