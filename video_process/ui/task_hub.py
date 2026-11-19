# -*- coding: utf-8 -*-
"""任务调度中心：唯一的 TaskRunner + 按发起页路由日志与进度。

主窗口与单功能窗口各持有一个 TaskHub，保证窗口内仍然只允许
一个任务同时运行；后台线程产生的日志、进度与结果都回传给
**发起任务的功能页**，而不是当前选中的页面。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QObject

from ..core.models import LogLevel, ProgressInfo, Settings, TaskResult
from ..param_spec import ToolDefinition
from .tasks import TaskRunner

#: 兜底日志回调：(message, level) -> None，通常写到系统日志
LogCallback = Callable[[str, LogLevel], None]


class TaskHub(QObject):
    """管理单个后台任务，并把输出路由到发起它的功能页。"""

    def __init__(
        self,
        log_fallback: Optional[LogCallback] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._fallback: LogCallback = log_fallback or (lambda _m, _l: None)
        self._owner: Optional[object] = None

        self._runner = TaskRunner(self)
        self._runner.logRequested.connect(self._on_log)
        self._runner.progressChanged.connect(self._on_progress)
        self._runner.finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._runner.running

    @property
    def owner(self) -> Optional[object]:
        """当前任务的发起页；空闲时为 None。"""
        return self._owner

    def is_cancelled(self) -> bool:
        return self._runner.is_cancelled()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def reserve(self, page: object) -> bool:
        """占位：已有任务在跑时返回 False。"""
        if self._runner.running:
            return False
        self._owner = page
        return True

    def release(self, page: object) -> None:
        """释放占位（同步任务结束或启动失败时调用）。"""
        if self._owner is page:
            self._owner = None

    def start(
        self,
        page: object,
        definition: ToolDefinition,
        values: Dict[str, Any],
        settings: Settings,
    ) -> bool:
        """启动后台任务；启动失败时释放占位。"""
        self._owner = page
        if self._runner.start(definition, values, settings):
            return True
        self._owner = None
        return False

    def cancel(self) -> None:
        self._runner.cancel()

    def wait(self, msecs: int = 3000) -> None:
        self._runner.wait(msecs)

    # ------------------------------------------------------------------
    # 信号路由
    # ------------------------------------------------------------------
    def _on_log(self, message: str, level: LogLevel) -> None:
        owner = self._owner
        if owner is not None:
            owner.log_message(message, level)  # type: ignore[attr-defined]
        else:
            self._fallback(str(message), level)

    def _on_progress(self, info: ProgressInfo) -> None:
        owner = self._owner
        if owner is not None:
            owner.update_progress(info)  # type: ignore[attr-defined]

    def _on_finished(self, result: TaskResult) -> None:
        owner, self._owner = self._owner, None
        if owner is not None:
            owner.finish_task(result)  # type: ignore[attr-defined]
            return
        level = LogLevel.SUCCESS if result.success else LogLevel.ERROR
        if result.message:
            self._fallback(result.message, level)
