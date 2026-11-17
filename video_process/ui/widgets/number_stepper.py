# -*- coding: utf-8 -*-
"""数值步进输入控件：输入框 + 「−」「+」按钮，点击一次按步长调整。

用于音量增益这类需要「一次调 1 个单位」的参数：
  · 按钮每点一次加减一个 step（可长按连续调整）
  · 输入框仍可直接键入小数
  · 超出 [minimum, maximum] 自动夹回边界
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QLocale, Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget


class NumberStepper(QWidget):
    """带减/加按钮的数值输入框。"""

    #: 数值变化（键入或按钮点击）
    valueChanged = pyqtSignal()

    def __init__(
        self,
        value: float = 0.0,
        step: float = 1.0,
        minimum: Optional[float] = None,
        maximum: Optional[float] = None,
        decimals: int = 1,
        placeholder: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._step = float(step) if step else 1.0
        self._min = minimum
        self._max = maximum
        self._decimals = max(0, int(decimals))
        self._last = self._clamp(self._to_float(value))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._minus = self._make_button("−")
        self._minus.clicked.connect(lambda: self.step_by(-1))
        layout.addWidget(self._minus)

        self._edit = QLineEdit(self)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setMinimumWidth(90)

        validator = QDoubleValidator(self)
        validator.setLocale(QLocale.c())   # 固定使用小数点，避免区域设置影响
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        validator.setDecimals(self._decimals)
        if minimum is not None:
            validator.setBottom(float(minimum))
        if maximum is not None:
            validator.setTop(float(maximum))
        self._edit.setValidator(validator)
        self._edit.setText(self._format(self._last))
        self._edit.textChanged.connect(lambda _text: self.valueChanged.emit())
        layout.addWidget(self._edit, 1)

        self._plus = self._make_button("+")
        self._plus.clicked.connect(lambda: self.step_by(1))
        layout.addWidget(self._plus)

    # ------------------------------------------------------------------
    # 值
    # ------------------------------------------------------------------
    @property
    def value(self) -> float:
        """当前数值（无法解析时回退到上一次有效值）。"""
        text = self._edit.text().strip()
        try:
            value = float(text)
        except ValueError:
            return self._last
        return self._clamp(value)

    def set_value(self, value: Any) -> None:
        target = self._clamp(self._to_float(value))
        text = self._format(target)
        self._last = float(text)   # 与显示保持一致，避免浮点误差累积
        if self._edit.text() != text:
            self._edit.setText(text)
        else:
            self.valueChanged.emit()

    def step_by(self, steps: int) -> None:
        """按步长调整：steps 为 -1 减一个 step，+1 加一个 step。"""
        self.set_value(self.value + steps * self._step)

    def set_invalid(self, invalid: bool) -> None:
        self._edit.setProperty("invalid", "true" if invalid else "false")
        self._edit.style().unpolish(self._edit)
        self._edit.style().polish(self._edit)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _make_button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("number-step")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedWidth(34)
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(400)
        button.setAutoRepeatInterval(80)
        return button

    def _format(self, value: float) -> str:
        if self._decimals <= 0:
            return str(int(round(value)))
        return f"{value:.{self._decimals}f}"

    def _clamp(self, value: float) -> float:
        if self._min is not None:
            value = max(float(self._min), value)
        if self._max is not None:
            value = min(float(self._max), value)
        return value

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
