# -*- coding: utf-8 -*-
"""PyQt6 主窗口：左侧功能导航 + 右侧功能页（参数 + 本页日志）+ 状态栏。

每个功能页各自持有日志与进度，后台任务的输出只回传给发起任务的功能页；
ffmpeg/AMF 自检、设置保存与任务调度等系统级消息集中到「系统日志」页。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Sequence, TextIO, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPalette,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .._frozen import resource_path
from .._io import ensure_utf8_stdio
from ..core.models import LogLevel, Settings
from ..defs import NAV_GROUPS, SYSTEM_GROUP, SYSTEM_TOOL_IDS, TOOL_DEFS
from ..param_spec import ToolDefinition
from ..settings_store import load_settings
from . import theme
from .feature_window import create_feature_window
from .task_hub import TaskHub
from .tool_page import SystemLogPage, ToolPage
from .window_shell import (
    HINT,
    EnvChecker,
    StatusBar,
    TitleBar,
    env_log_lines,
)

#: 导航项自定义数据：分组标题标记
ROLE_IS_GROUP = Qt.ItemDataRole.UserRole + 1
#: 导航项自定义数据：工具 id
ROLE_TOOL_ID = Qt.ItemDataRole.UserRole

#: 系统日志页的 UI 专用 id（不进入 defs，因此不会多出 CLI 子命令）
SYSTEM_LOG_ID = "system-log"

#: 应用图标（相对项目根 / 打包资源根）。缺失时程序照常运行，
#: 只是任务栏与标题栏用 Qt 默认图标
APP_ICON_REL = os.path.join("assets", "app.ico")

def load_app_icon() -> Optional[QIcon]:
    """加载应用图标，找不到返回 None。

    源码运行与打包运行共用同一份相对路径，由 :func:`resource_path`
    负责在两种布局间切换。
    """
    path = resource_path(APP_ICON_REL)
    if not path:
        return None
    icon = QIcon(path)
    return None if icon.isNull() else icon


class _NavDelegate(QStyledItemDelegate):
    """导航列表绘制：分组标题用小号青蓝字，工具项选中时左侧强调条。"""

    def paint(self, painter, option, index) -> None:  # noqa: D102
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()

        if index.data(ROLE_IS_GROUP) is True:
            # 分组标题：不参与选中与悬停高亮
            opt.state &= ~QStyle.StateFlag.State_Selected
            opt.state &= ~QStyle.StateFlag.State_MouseOver
            opt.backgroundBrush = QBrush(QColor(theme.BG_PANEL))
            opt.palette.setColor(
                QPalette.ColorRole.Text, QColor(theme.ACCENT_DEEP)
            )
            font = QFont(opt.font)
            font.setPointSize(9)
            font.setBold(True)
            opt.font = font
        else:
            selected = bool(opt.state & QStyle.StateFlag.State_Selected)
            hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
            if selected:
                opt.backgroundBrush = QBrush(QColor(theme.BG_RAISED))
                opt.palette.setColor(
                    QPalette.ColorRole.Text, QColor(theme.ACCENT)
                )
                font = QFont(opt.font)
                font.setBold(True)
                opt.font = font
            elif hovered:
                opt.backgroundBrush = QBrush(QColor(theme.BG_RAISED))
                opt.palette.setColor(
                    QPalette.ColorRole.Text, QColor(theme.TEXT)
                )

        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget
        )

    def sizeHint(self, option, index) -> QSize:  # noqa: D102
        height = 26 if index.data(ROLE_IS_GROUP) is True else 32
        return QSize(option.rect.width(), height)


class VideoProcessWindow(QMainWindow):
    """视频处理工具集主窗口，同时充当各功能页的 WindowHost。"""

    def __init__(self) -> None:
        super().__init__()
        self.settings: Settings = load_settings()

        self._nav_items: List[Optional[str]] = []
        self._pages: Dict[str, ToolPage] = {}
        self._system_page: Optional[SystemLogPage] = None
        self.hub = TaskHub(self.log_system, self)

        self.setWindowTitle(f"视频处理工具集 v{__version__}")
        self.resize(1240, 840)
        self.setMinimumSize(940, 620)

        self.status_bar = StatusBar.attach(self, HINT)
        self._build_ui()
        self._build_shortcuts()

        # 界面就绪后再做环境自检
        QTimer.singleShot(60, self._check_environment)

    # ==================================================================
    # 布局
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("root")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._title_bar = TitleBar("视频处理工具集", central)
        root.addWidget(self._title_bar)

        body = QSplitter(Qt.Orientation.Horizontal, central)
        body.setHandleWidth(1)
        # 先建内容区：导航初始化时会立刻挂载首个功能页，需要 _stack 已就绪
        content_pane = self._build_content_pane()
        nav_pane = self._build_nav_pane()
        body.addWidget(nav_pane)
        body.addWidget(content_pane)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([220, 1020])
        body.setCollapsible(0, False)
        body.setCollapsible(1, False)
        root.addWidget(body, 1)

        self.setCentralWidget(central)

    def _build_nav_pane(self) -> QWidget:
        pane = QFrame(self)
        pane.setObjectName("nav-pane")
        pane.setMinimumWidth(180)

        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(0)

        nav_title = QLabel("功能", pane)
        nav_title.setObjectName("nav-title")
        layout.addWidget(nav_title)

        self._nav_list = QListWidget(pane)
        self._nav_list.setObjectName("nav-list")
        self._nav_list.setItemDelegate(_NavDelegate(self._nav_list))
        layout.addWidget(self._nav_list, 1)

        # 先连接再填充，保证初始选中项也能触发页面挂载
        self._nav_list.currentRowChanged.connect(self._on_nav_changed)
        self._build_nav()
        return pane

    def _build_content_pane(self) -> QWidget:
        pane = QWidget(self)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget(pane)
        # 系统日志页常驻：环境自检可能早于用户访问它就产生消息
        self._system_page = SystemLogPage(self._stack)
        self._stack.addWidget(self._system_page)
        layout.addWidget(self._stack, 1)
        return pane

    # ==================================================================
    # 导航
    # ==================================================================
    def _build_nav(self) -> None:
        for group_name, tool_ids in NAV_GROUPS:
            self._add_nav_group(group_name)
            for tool_id in tool_ids:
                definition = TOOL_DEFS[tool_id]
                self._add_nav_tool(
                    definition.title, tool_id, definition.title
                )

        self._add_nav_group(SYSTEM_GROUP)
        self._add_nav_tool(
            "系统日志", SYSTEM_LOG_ID, "ffmpeg/AMF 自检与任务调度信息"
        )
        for tool_id in SYSTEM_TOOL_IDS:
            definition = TOOL_DEFS[tool_id]
            self._add_nav_tool(definition.title, tool_id, definition.title)

        for index, tool_id in enumerate(self._nav_items):
            if tool_id:
                self._nav_list.setCurrentRow(index)
                break

    def _add_nav_group(self, name: str) -> None:
        item = QListWidgetItem(name)
        item.setData(ROLE_IS_GROUP, True)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._nav_list.addItem(item)
        self._nav_items.append(None)

    def _add_nav_tool(self, label: str, tool_id: str, tooltip: str) -> None:
        item = QListWidgetItem(f"  {label}")
        item.setData(ROLE_TOOL_ID, tool_id)
        item.setToolTip(tooltip)
        self._nav_list.addItem(item)
        self._nav_items.append(tool_id)

    def _on_nav_changed(self, row: int) -> None:
        if not (0 <= row < len(self._nav_items)):
            return
        tool_id = self._nav_items[row]
        if tool_id is None:
            return
        self.switch_to(tool_id)

    def switch_to(self, tool_id: str) -> None:
        """切换到指定功能页（页面按需创建并缓存，日志随之保留）。"""
        page = self._ensure_page(tool_id)
        if page is None:
            return

        self._stack.setCurrentWidget(page)

        if tool_id == SYSTEM_LOG_ID:
            return

        owner = self.hub.owner
        if self.hub.running and owner is not None and owner is not page:
            title = getattr(getattr(owner, "definition", None), "title", "")
            self.log_system(
                f"（{title} 仍在后台运行，结果会回传给发起面板）",
                LogLevel.WARNING,
            )

        if tool_id == "settings" and isinstance(page, ToolPage):
            panel = page.panel
            if hasattr(panel, "load_from_settings"):
                panel.load_from_settings()

    # ==================================================================
    # 快捷键
    # ==================================================================
    def _build_shortcuts(self) -> None:
        self._add_shortcut("Ctrl+Q", self.close)
        self._add_shortcut("Ctrl+C", self.action_cancel_task)
        self._add_shortcut("Ctrl+L", self.action_clear_log)
        self._add_shortcut("F1", self.action_toggle_help)

    def _add_shortcut(self, sequence: str, slot) -> QAction:
        action = QAction(self)
        action.setShortcut(QKeySequence(sequence))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    # ==================================================================
    # 环境自检
    # ==================================================================
    def _check_environment(self) -> None:
        self._env_checker = EnvChecker(self)
        self._env_checker.finished.connect(self._render_env)
        self._env_checker.start()

    def _render_env(
        self, ok: bool, version: str, amf: List[str]
    ) -> None:
        self._title_bar.env.render(ok, version, amf)
        for line, level in env_log_lines(ok, version):
            self.log_system(line, level)
        if not ok:
            self.notify(
                "未检测到 ffmpeg/ffprobe，所有功能将不可用", "error", timeout=10
            )

    # ==================================================================
    # 日志 / 通知
    # ==================================================================
    def log_system(
        self, message: str, level: LogLevel = LogLevel.INFO
    ) -> None:
        page = self._system_page
        if page is not None:
            page.log_message(message, level)

    def notify(
        self, message: str, severity: str = "information", timeout: int = 5
    ) -> None:
        self.status_bar.notify(message, severity, timeout)

    def action_clear_log(self) -> None:
        """清空当前页面（功能页或系统日志页）的日志。"""
        page = self._stack.currentWidget()
        clear = getattr(page, "clear_log", None)
        if callable(clear):
            clear()

    def action_toggle_help(self) -> None:
        QMessageBox.information(
            self,
            "快捷键",
            "Ctrl+Q  退出\n"
            "Ctrl+C  停止任务\n"
            "Ctrl+L  清空当前功能日志\n"
            "F1      显示本帮助\n\n"
            "导航：↑↓ 切换功能，Enter 选中\n"
            "参数：Tab 在控件间移动，路径可直接拖入\n"
            "说明：点标题右侧的「说明」展开功能说明\n"
            "日志：每个功能页下方各有一份，默认收起，"
            "点标题行（箭头或文字）展开（执行任务时自动展开）",
        )

    # ==================================================================
    # 任务调度（WindowHost 实现）
    # ==================================================================
    def reserve_task(self, page: object) -> bool:
        if not self.hub.reserve(page):
            self.notify("已有任务正在运行", "warning", timeout=3)
            self.log_system(
                "已有任务正在运行，已拒绝新的执行请求", LogLevel.WARNING
            )
            return False
        title = getattr(getattr(page, "definition", None), "title", "")
        self.status_bar.set_running(True, title)
        return True

    def release_task(self, page: object) -> None:
        self.hub.release(page)
        if not self.hub.running:
            self.status_bar.set_running(False)

    def start_task(
        self, page: object, definition: ToolDefinition, values: dict
    ) -> bool:
        return self.hub.start(page, definition, values, self.settings)

    def cancel_task(self) -> None:
        if not self.hub.running:
            self.notify("当前没有正在运行的任务", "information", timeout=2)
            return
        self.hub.cancel()
        self.log_system(
            "正在请求停止…（等待当前 ffmpeg 进程退出）", LogLevel.WARNING
        )

    def action_cancel_task(self) -> None:
        self.cancel_task()

    def is_task_cancelled(self) -> bool:
        return self.hub.is_cancelled()

    # ==================================================================
    # 供功能页调用的跨页能力
    # ==================================================================
    def _ensure_page(self, tool_id: str) -> Optional[QWidget]:
        """按需创建并缓存功能页（不切换导航）。"""
        if tool_id == SYSTEM_LOG_ID:
            return self._system_page

        definition = TOOL_DEFS.get(tool_id)
        if definition is None:
            return None

        page = self._pages.get(tool_id)
        if page is None:
            page = ToolPage(definition, self, self._stack)
            self._pages[tool_id] = page
            self._stack.addWidget(page)
        return page

    def fill_remove_segments(
        self,
        csv: str = "",
        input_path: str = "",
        navigate: bool = True,
    ) -> None:
        """把广告区间 / 同名视频填入「片段删除」页面。

        navigate=False 时不切走导航：时间线计算器拖入文件后
        仍停在原页面展示结果，用户切过去即可看到已填好的参数。
        """
        page = self._ensure_page("remove-segments")
        if not isinstance(page, ToolPage):
            return

        if navigate:
            for index, tool_id in enumerate(self._nav_items):
                if tool_id == "remove-segments":
                    self._nav_list.setCurrentRow(index)
                    break

        panel = page.panel
        try:
            parts: List[str] = []
            if csv and panel.fill_value("segments", csv):
                parts.append(f"时间段 {csv}")
            if input_path and panel.fill_value("input_path", input_path):
                parts.append(f"视频 {input_path}")
            if parts:
                message = "已填入「片段删除」：" + "；".join(parts)
                page.log_message(message, LogLevel.SUCCESS)
                self.notify(message, "information", timeout=5)
            elif csv or input_path:
                self.notify("填入失败：未找到对应字段", "error", timeout=5)
        except Exception as exc:  # noqa: BLE001 - 回填失败不应中断界面
            self.notify(f"填入失败: {exc}", "error", timeout=5)

    def reload_defaults(self, keep: object = None) -> None:
        """全局设置变更后刷新各页参数默认值（各页日志保留）。

        参数默认值在面板创建时从设置快照，因此只替换面板对象，
        外层功能页与其日志视图不动。正在运行任务的页面跳过重建。
        """
        for page in list(self._pages.values()):
            if page is keep:
                continue
            page.rebuild_panel()

    # ==================================================================
    # 关闭保护
    # ==================================================================
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hub.running:
            answer = QMessageBox.question(
                self,
                "任务正在运行",
                "当前任务尚未结尾，关闭窗口会中断它。确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.hub.cancel()
            self.hub.wait(3000)
        event.accept()


def create_window() -> VideoProcessWindow:
    """创建主窗口（不进入事件循环，便于测试驱动）。"""
    return VideoProcessWindow()


# ---------------------------------------------------------------------------
# 启动参数
# ---------------------------------------------------------------------------
def _resolve_startup(
    argv: Sequence[str],
) -> Tuple[Optional[str], List[str], str]:
    """解析启动参数：功能名 / --list / --help。

    返回 (tool_id, 传给 Qt 的剩余参数, 动作)，
    动作为 ``gui`` / ``list`` / ``help`` / ``error``。
    """
    tool_id: Optional[str] = None
    qt_args: List[str] = []

    for token in argv:
        if token == "--list":
            return None, [], "list"
        if token in ("-h", "--help") and tool_id is None and not qt_args:
            return None, [], "help"
        if tool_id is None and not token.startswith("-"):
            tool_id = token
        else:
            qt_args.append(token)

    if tool_id is None:
        return None, qt_args, "gui"
    if tool_id not in TOOL_DEFS:
        return tool_id, qt_args, "error"
    return tool_id, qt_args, "gui"


def _print_tools(stream: Optional[TextIO] = None) -> None:
    stream = stream or sys.stdout
    print("可用功能（python main.py <功能名> 单独打开）:", file=stream)
    groups = list(NAV_GROUPS) + [(SYSTEM_GROUP, SYSTEM_TOOL_IDS)]
    for group, tool_ids in groups:
        print(f"  [{group}]", file=stream)
        for tool_id in tool_ids:
            print(
                f"    {tool_id:<18} {TOOL_DEFS[tool_id].title}",
                file=stream,
            )


def _print_usage(stream: Optional[TextIO] = None) -> None:
    stream = stream or sys.stdout
    print(
        "用法:\n"
        "  python main.py                打开完整界面\n"
        "  python main.py <功能名>        只打开该功能的独立窗口\n"
        "  python main.py --list          列出可用功能名\n\n"
        "示例: python main.py merge",
        file=stream,
    )


def run_app(argv: Optional[Sequence[str]] = None) -> int:
    """启动 Qt 图形界面（主窗口或单功能窗口）。"""
    ensure_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    tool_id, qt_args, action = _resolve_startup(args)

    if action == "list":
        _print_tools()
        return 0
    if action == "help":
        _print_usage()
        return 0
    if action == "error":
        print(f"未知功能: {tool_id}", file=sys.stderr)
        _print_tools(sys.stderr)
        print("", file=sys.stderr)
        _print_usage(sys.stderr)
        return 2

    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(
            [sys.argv[0] if sys.argv else "video-process", *qt_args]
        )

    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    theme.apply_theme(app)

    window = (
        create_feature_window(tool_id) if tool_id else create_window()
    )
    if icon is not None:
        window.setWindowIcon(icon)
    window.show()

    if owns_app:
        return app.exec()
    return 0
