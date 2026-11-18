# -*- coding: utf-8 -*-
"""时间线计算器面板。

纯计算，不进后台线程；结果直接展示在面板内三块区域：
    1. 重算后的时间线
    2. 计算方法说明
    3. 广告区间 CSV（可一键填入「片段删除」）

输入区支持把 ``<文件名>.总结.txt`` 直接拖进来：读取内容后按同目录同名
视频自动计算，广告区间与视频一并填入「片段删除」，修订结果另存为
``<文件名>.总结.修订.txt``。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

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

from ...core.models import LogLevel, TaskResult, ToolContext
from ...core.textio import read_text
from ...tools.timeline import (
    calculate_timeline,
    find_same_name_video,
    save_revision,
)
from ..host import PanelHost
from ...param_spec import ToolDefinition
from .base import ToolPanel


class TimelinePanel(ToolPanel):
    """时间线计算器：多行输入 + 结果展示 + 复制 / 回填。"""

    def __init__(
        self,
        definition: ToolDefinition,
        host: PanelHost,
        parent: QWidget | None = None,
    ) -> None:
        self._last_csv = ""
        #: 拖入的时间线文件；非空时每次计算都会另存修订版
        self._source_file = ""
        #: 与时间线文件同目录的同名视频（可能为空）
        self._source_video = ""
        super().__init__(definition, host, parent)

    # ==================================================================
    # 结果区
    # ==================================================================
    def build_extra(self, content: QWidget, layout: QVBoxLayout) -> None:
        self._source_label = QLabel("", content)
        self._source_label.setProperty("role", "param-help")
        self._source_label.setWordWrap(True)
        self._source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._source_label.hide()
        layout.addWidget(self._source_label)

        section = QLabel("计算结果", content)
        section.setProperty("role", "section")
        layout.addWidget(section)

        self._steps_label = QLabel("", content)
        self._steps_label.setProperty("role", "param-help")
        self._steps_label.setWordWrap(True)
        self._steps_label.hide()
        layout.addWidget(self._steps_label)

        self._result_view = QPlainTextEdit(content)
        self._result_view.setReadOnly(True)
        self._result_view.setFixedHeight(180)
        self._result_view.hide()
        layout.addWidget(self._result_view)

        self._csv_label = QLabel("", content)
        self._csv_label.setProperty("role", "param-help")
        self._csv_label.setWordWrap(True)
        self._csv_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._csv_label.hide()
        layout.addWidget(self._csv_label)

    def build_footer(self, layout: QVBoxLayout) -> None:
        """复制 / 回填按钮常驻在滚动区之外，避免结果很长时要滚到底。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(8)

        copy_button = QPushButton("复制到剪贴板", self)
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(self._copy_result)
        row.addWidget(copy_button)

        fill_button = QPushButton("填入「片段删除」", self)
        fill_button.setCursor(Qt.CursorShape.PointingHandCursor)
        fill_button.clicked.connect(self._fill_remove_segments)
        row.addWidget(fill_button)

        row.addStretch(1)
        layout.addLayout(row)

    # ==================================================================
    # 执行：纯计算，主线程同步完成
    # ==================================================================
    def start_task(self, values: Dict[str, Any]) -> None:
        self.set_running(True)
        if not self.host.begin_task(self.definition.title):
            self.set_running(False)
            return

        ctx = ToolContext(
            on_log=lambda msg, lvl: self.host.log_message(msg, lvl),
            on_progress=lambda info: self.host.update_progress(info),
            is_cancelled=lambda: self.host.is_task_cancelled(),
        )
        try:
            result = calculate_timeline(values.get("text", ""), ctx=ctx)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免界面静默崩溃
            result = TaskResult(success=False, message=f"执行异常: {exc}")

        self.host.end_task(result)
        self.on_result(result)

    # ==================================================================
    # 结果展示
    # ==================================================================
    def on_result(self, result: TaskResult) -> None:
        self.set_running(False)

        if not result.success:
            self._clear_result()
            self._steps_label.setText(result.message)
            self._steps_label.setProperty("role", "status-err")
            self._steps_label.style().unpolish(self._steps_label)
            self._steps_label.style().polish(self._steps_label)
            self._steps_label.show()
            return

        # warnings 最后一行是广告 CSV
        warnings: List[str] = list(result.warnings)
        ad_csv = ""
        if warnings and warnings[-1].startswith("广告区间 CSV:"):
            ad_csv = warnings.pop().split(":", 1)[1].strip()

        self._steps_label.setProperty("role", "param-help")
        self._steps_label.style().unpolish(self._steps_label)
        self._steps_label.style().polish(self._steps_label)
        self._steps_label.setText("\n".join(warnings))
        self._steps_label.show()

        self._result_view.setPlainText(
            result.outputs[0] if result.outputs else ""
        )
        self._result_view.show()

        self._csv_label.setText(f"广告区间 CSV: {ad_csv}")
        self._csv_label.setVisible(bool(ad_csv))
        self._last_csv = ad_csv

        if self._source_file:
            self._apply_source_revision(
                result.outputs[0] if result.outputs else "", ad_csv
            )

        for line in warnings:
            self.host.log_message(line, LogLevel.INFO)
        if ad_csv:
            self.host.log_message(f"广告区间 CSV: {ad_csv}", LogLevel.SUCCESS)

    def _clear_result(self) -> None:
        self._steps_label.setText("")
        self._steps_label.hide()
        self._result_view.setPlainText("")
        self._result_view.hide()
        self._csv_label.setText("")
        self._csv_label.hide()
        self._last_csv = ""

    def reset_params(self) -> None:
        super().reset_params()
        self._clear_result()
        self._source_file = ""
        self._source_video = ""
        self._source_label.setText("")
        self._source_label.hide()

    # ==================================================================
    # 拖入时间线 txt：读内容 → 找同名视频 → 自动计算 → 另存修订版
    # ==================================================================
    _TEXT_SUFFIXES = (".txt",)

    def _on_value_changed(self, *_args) -> None:
        """手动改动文本即与原文件脱钩，避免把别的内容写进旧修订版。"""
        self._source_file = ""
        self._source_video = ""
        label = getattr(self, "_source_label", None)
        if label is not None:
            label.hide()
        super()._on_value_changed(*_args)

    def on_text_file_dropped(self, name: str, path: str) -> None:
        if name != "text":
            super().on_text_file_dropped(name, path)
            return

        if os.path.splitext(path)[1].lower() not in self._TEXT_SUFFIXES:
            self.host.notify(
                f"只能拖入 txt 文本：{os.path.basename(path)}",
                "warning",
                timeout=5,
            )
            return

        try:
            content = read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.host.notify(
                f"读取失败：{os.path.basename(path)}（{exc}）",
                "error",
                timeout=5,
            )
            return

        if not content.strip():
            self.host.notify(
                f"文件为空：{os.path.basename(path)}", "warning", timeout=4
            )
            return

        # 先写文本（会触发 _on_value_changed 清空来源），再记录来源
        self.fill_value("text", content)
        self._source_file = os.path.abspath(path)
        self._source_video = find_same_name_video(self._source_file)

        video_name = (
            os.path.basename(self._source_video)
            if self._source_video else "未找到同名视频"
        )
        self._source_label.setText(
            f"来源：{os.path.basename(self._source_file)}　·　"
            f"同名视频：{video_name}"
        )
        self._source_label.show()

        self.host.log_message(f"已载入时间线：{self._source_file}", LogLevel.INFO)
        if self._source_video:
            self.host.log_message(
                f"同目录同名视频：{self._source_video}", LogLevel.INFO
            )
        else:
            self.host.log_message("同目录下未找到同名视频", LogLevel.WARNING)
            self.host.notify(
                "同目录下未找到同名视频，请稍后在「片段删除」中手动选择",
                "warning",
                timeout=6,
            )

        # 拖进来即算：广告区间与修订版都依赖这次计算结果
        self.request_run()

    def _apply_source_revision(self, text: str, csv: str) -> None:
        """计算结果回写：另存修订版，并把广告区间 / 视频填入「片段删除」。"""
        if text:
            try:
                target = save_revision(self._source_file, text)
            except OSError as exc:
                self.host.log_message(
                    f"修订版保存失败：{exc}", LogLevel.ERROR
                )
                self.host.notify(
                    f"修订版保存失败：{exc}", "error", timeout=6
                )
            else:
                self.host.log_message(
                    f"已保存修订版：{target}", LogLevel.SUCCESS
                )
                self.host.notify(
                    f"已保存 {os.path.basename(target)}",
                    "information",
                    timeout=4,
                )

        if csv:
            self.host.fill_remove_segments(
                csv, input_path=self._source_video, navigate=False
            )

    # ==================================================================
    # 复制 / 回填
    # ==================================================================
    def _copy_result(self) -> None:
        text = self._result_view.toPlainText()
        if not text:
            self.host.notify("没有可复制的结果", "warning", timeout=3)
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            self.host.notify("已复制到剪贴板", "information", timeout=3)
        else:
            self.host.notify(
                "复制失败，请手动选择文本复制", "warning", timeout=4
            )

    def _fill_remove_segments(self) -> None:
        csv = getattr(self, "_last_csv", "")
        if not csv:
            self.host.notify("请先执行一次计算", "warning", timeout=3)
            return
        self.host.fill_remove_segments(csv)
