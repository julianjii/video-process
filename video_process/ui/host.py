# -*- coding: utf-8 -*-
"""面板 / 功能页 / 窗口之间的窄接口。

三层结构：
    ToolPanel  --(PanelHost)-->  ToolPage  --(WindowHost)-->  窗口

面板只依赖 :class:`PanelHost`，功能页负责把日志与进度落到本页；
窗口级能力（通知、任务调度、跨页回填）由窗口实现 :class:`WindowHost`。
这样面板不直接依赖 QMainWindow，便于在 offscreen 冒烟测试中替换宿主。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ..core.models import LogLevel, ProgressInfo, Settings, TaskResult
from ..param_spec import ToolDefinition


@runtime_checkable
class PanelHost(Protocol):
    """功能页向面板暴露的能力（一个功能页 = 一个 PanelHost）。"""

    #: 全局设置（各工具的默认值来源）
    settings: Settings

    def log_message(self, message: str,
                    level: LogLevel = LogLevel.INFO) -> None:
        """写本功能页的日志。"""
        ...

    def log_system(self, message: str,
                   level: LogLevel = LogLevel.INFO) -> None:
        """写系统日志（窗口级消息）。"""
        ...

    def update_progress(self, info: ProgressInfo) -> None:
        """更新本功能页的进度。"""
        ...

    def notify(self, message: str, severity: str = "information",
               timeout: int = 5) -> None: ...

    def is_task_cancelled(self) -> bool: ...

    def begin_task(self, title: str) -> bool:
        """进入运行态；已有任务在跑时返回 False。"""
        ...

    def end_task(self, result: Optional[TaskResult] = None) -> None: ...

    def run_task(self, definition: ToolDefinition, values: dict,
                 panel: object = None) -> bool:
        """在后台线程执行工具；启动失败返回 False。"""
        ...

    def cancel_task(self) -> None: ...

    def fill_remove_segments(
        self,
        csv: str = "",
        input_path: str = "",
        navigate: bool = True,
    ) -> None:
        """把广告区间与同名视频填入「片段删除」页面。"""
        ...

    def reload_panels(self, keep: object = None) -> None:
        """全局设置变更后刷新各页参数默认值（保留日志）。

        keep: 不重建的面板，通常是发起保存的设置面板自身。
        """
        ...


@runtime_checkable
class WindowHost(Protocol):
    """功能页向窗口请求的能力（主窗口与单功能窗口各自实现）。"""

    #: 全局设置（可整体替换）
    settings: Settings

    def notify(self, message: str, severity: str = "information",
               timeout: int = 5) -> None: ...

    def log_system(self, message: str,
                   level: LogLevel = LogLevel.INFO) -> None:
        """写系统日志；单功能窗口没有系统页时并入本页日志。"""
        ...

    def reserve_task(self, page: object) -> bool:
        """为功能页占位；已有任务在跑时返回 False。"""
        ...

    def release_task(self, page: object) -> None: ...

    def start_task(self, page: object, definition: ToolDefinition,
                   values: dict) -> bool: ...

    def cancel_task(self) -> None: ...

    def is_task_cancelled(self) -> bool: ...

    def fill_remove_segments(
        self,
        csv: str = "",
        input_path: str = "",
        navigate: bool = True,
    ) -> None: ...

    def reload_defaults(self, keep: object = None) -> None:
        """按当前设置刷新各功能页的参数默认值。"""
        ...
