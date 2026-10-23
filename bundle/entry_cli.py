# -*- coding: utf-8 -*-
"""打包后的命令行入口（对应「视频处理工具集-命令行.exe」，带控制台窗口）。

与源码模式的 `python -m video_process.cli` 共用同一份实现。
"""

from __future__ import annotations

import sys

from video_process.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
