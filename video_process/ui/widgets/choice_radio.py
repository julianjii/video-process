# -*- coding: utf-8 -*-
"""单选按钮组：把 choice 参数渲染成一行互斥的 QRadioButton。

与下拉框（QComboBox）取值完全一致：对外只暴露用户数据（choices 的第二个值），
因此 ParamSpec 的默认值、命令行取值都不受影响。
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QRadioButton,
    QWidget,
)


class ChoiceRadio(QWidget):
    """互斥单选项组（横向排列）。"""

    #: 选中项变化
    valueChanged = pyqtSignal()

    def __init__(
        self,
        choices: Sequence[Tuple[str, Any]],
        value: Any = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._buttons: List[Tuple[QRadioButton, Any]] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.idToggled.connect(self._on_toggled)

        for index, (text, data) in enumerate(choices):
            button = QRadioButton(str(text), self)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(button, index)
            layout.addWidget(button)
            self._buttons.append((button, data))

        layout.addStretch(1)
        self._group = group
        self.set_value(value)

    # ------------------------------------------------------------------
    # 值
    # ------------------------------------------------------------------
    @property
    def value(self) -> Any:
        """当前选中项的数据（没有选中时回退到第一项）。"""
        for button, data in self._buttons:
            if button.isChecked():
                return data
        return self._buttons[0][1] if self._buttons else None

    def set_value(self, value: Any) -> None:
        for button, data in self._buttons:
            if data == value:
                if not button.isChecked():
                    button.setChecked(True)
                return

    def set_invalid(self, invalid: bool) -> None:
        for button, _data in self._buttons:
            button.setProperty("invalid", "true" if invalid else "false")
            button.style().unpolish(button)
            button.style().polish(button)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_toggled(self, _index: int, checked: bool) -> None:
        if checked:
            self.valueChanged.emit()


__all__ = ["ChoiceRadio"]
