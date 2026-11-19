#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图形界面离屏冒烟：每个功能页独立日志、系统日志页、单功能窗口。

用法:
    python gui_check.py

脚本会把 Qt 切到 offscreen 平台，不需要真实显示器，也不会启动事件循环。
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


def check_pages(win, tool_ids):
    """逐个功能页写入带标记的日志，返回 {tool_id: page}。"""
    pages = {}
    for tool_id in tool_ids:
        win.switch_to(tool_id)
        page = win._pages.get(tool_id)
        check(f"页面已创建 {tool_id}", page is not None)
        if page is None:
            continue
        pages[tool_id] = page
        page.log_message(f"marker-{tool_id}")
        page.flush_log()
        check(
            f"{tool_id} 日志落到本页",
            f"marker-{tool_id}" in page.log_view.toPlainText(),
        )
    return pages


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    from PyQt6.QtWidgets import QApplication

    from video_process import defs
    from video_process.core.models import TaskResult
    from video_process.param_spec import ToolDefinition
    from video_process.ui import theme
    from video_process.ui.app import (
        SYSTEM_LOG_ID,
        VideoProcessWindow,
        _resolve_startup,
        run_app,
    )
    from video_process.ui.feature_window import FeatureWindow

    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)

    tool_ids = [
        tool_id for _group, ids in defs.NAV_GROUPS for tool_id in ids
    ]

    print("\n[1] 主窗口：每个功能页独立日志")
    win = VideoProcessWindow()
    pages = check_pages(win, tool_ids)

    cross = []
    for tool_id, page in pages.items():
        text = page.log_view.toPlainText()
        for other in pages:
            if other != tool_id and f"marker-{other}" in text:
                cross.append((tool_id, other))
    check("功能页日志互不串台", not cross, str(cross[:3]))

    print("\n[2] 系统日志页")
    win.switch_to(SYSTEM_LOG_ID)
    win.log_system("system-marker")
    win._system_page.flush_log()
    check(
        "系统日志页收到消息",
        "system-marker" in win._system_page.log_view.toPlainText(),
    )
    leaked = [
        tool_id
        for tool_id, page in pages.items()
        if "system-marker" in page.log_view.toPlainText()
    ]
    check("系统消息不进功能页", not leaked, str(leaked[:3]))

    print("\n[3] 设置刷新后面板重建但日志保留")
    page = pages.get("merge")
    if page is not None:
        page.log_message("keep-marker")
        page.flush_log()
        win.reload_defaults(keep=None)
        page.flush_log()
        check(
            "重建面板后日志仍在",
            "keep-marker" in page.log_view.toPlainText(),
        )
        check(
            "重建后面板可用",
            page.panel is not None and not page.panel.running,
        )

    print("\n[4] 单功能窗口")
    for tool_id in ("merge", "timeline", "settings"):
        fw = FeatureWindow(tool_id)
        fw.page.log_message(f"feature-{tool_id}")
        fw.page.flush_log()
        check(
            f"{tool_id} 独立窗口有本页日志",
            f"feature-{tool_id}" in fw.page.log_view.toPlainText(),
        )
        fw.close()

    timeline = FeatureWindow("timeline")
    timeline.fill_remove_segments("1:00-2:00")
    timeline.page.flush_log()
    check(
        "单窗口「填入片段删除」兜底为剪贴板",
        "剪贴板" in timeline.page.log_view.toPlainText(),
    )
    timeline.close()

    print("\n[5] 启动参数")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = run_app(["--list"])
    check("--list 返回 0", code == 0, f"code={code}")
    check(
        "--list 列出功能",
        "merge" in out.getvalue() and "timeline" in out.getvalue(),
    )

    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(
        io.StringIO()
    ):
        code = run_app(["no-such-tool"])
    check("未知功能返回 2", code == 2, f"code={code}")
    check("未知功能给出提示", "未知功能" in err.getvalue())

    tool_id, qt_args, action = _resolve_startup(
        ["merge", "-platform", "offscreen"]
    )
    check(
        "解析功能名与 Qt 参数",
        tool_id == "merge"
        and action == "gui"
        and qt_args == ["-platform", "offscreen"],
        f"{tool_id} {qt_args} {action}",
    )

    print("\n[6] 后台任务日志按发起页路由")

    def probe(ctx, **_values):
        ctx.log("async-marker")
        time.sleep(0.2)
        return TaskResult(success=True, message="async-done")

    probe_def = ToolDefinition(
        id="async-probe",
        title="后台任务探针",
        group="测试",
        specs=[],
        runner=probe,
    )

    win.switch_to("merge")
    merge_page = pages["merge"]
    audio_page = pages["audio"]
    check("启动后台任务", merge_page.run_task(probe_def, {}, panel=merge_page))
    check(
        "运行中拒绝第二个任务",
        pages["trim-edges"].begin_task("冲突任务") is False,
    )
    win.switch_to("audio")

    deadline = time.time() + 5
    while win.hub.running and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    merge_page.flush_log()
    audio_page.flush_log()
    win._system_page.flush_log()
    merge_text = merge_page.log_view.toPlainText()
    check(
        "任务日志与结果回到发起页",
        "async-marker" in merge_text and "async-done" in merge_text,
    )
    check(
        "切换后目标页没有该任务日志",
        "async-marker" not in audio_page.log_view.toPlainText(),
    )
    check(
        "调度冲突写入系统日志",
        "已有任务正在运行" in win._system_page.log_view.toPlainText(),
    )
    check(
        "任务结束状态栏回到空闲",
        win.status_bar.state.text() == "空闲",
        win.status_bar.state.text(),
    )

    win.close()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败项:")
        for item in FAIL:
            print(f"  - {item}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
