# 视频处理工具集

基于 [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) 的视频/字幕处理工具集，同一套功能提供**图形界面**与**命令行**两种用法。

| 入口 | 命令 |
|---|---|
| 图形界面 | `python main.py` |
| 命令行 | `python -m video_process.cli <功能名> [参数]` |

---

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python ≥ 3.9 | 已实测 3.11。**仅源码运行需要**，打包后的 exe 不需要 |
| `PyQt6` ≥ 6.5 | `pip install -r requirements.txt`。仅界面与打包需要 |
| ffmpeg / ffprobe | 需加入 `PATH`。AMD 相关功能需要**编译时启用 AMF** 的构建 |
| `opencc-python-reimplemented` ≥ 0.1.6 | 仅「字幕繁简转换」需要，缺失时该工具会提示安装命令 |

> 打包成 exe 后 ffmpeg 会一起内置，运行机无需安装（见「打包为可执行程序」）。

验证环境：

```powershell
python -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
```

> 界面启动时会在标题栏自动检测 ffmpeg/ffprobe 与 AMD AMF 编码器，
> 并以状态芯片显示检测结果。AMF 不可用时，仅 AMD 相关模式会失败，其余功能不受影响。

---

## 启动

```powershell
# 完整界面（会弹出控制台窗口）
python main.py

# 只打开某一个功能的独立窗口（参数区 + 该功能自己的日志区）
python main.py merge
python main.py remove-segments

# 列出全部功能名
python main.py --list

# 完整界面（无控制台黑窗）
run.bat
```

单功能命令使用与 CLI 相同的功能名；`settings` 也可以单独打开。
单功能窗口没有左侧导航，关闭时同样会在任务运行中提示确认。

### 界面概览

![界面概览](assets/screenshot.jpg)

每个功能页都由「参数区 + 本页日志与进度」组成，中间分隔条可拖动调整高度；
切换功能只显示该功能的日志，切回后历史仍在。后台任务运行期间切换功能，
日志与进度仍只写回发起任务的那个功能页。

为把窗口高度留给参数，功能说明默认收起（点标题右侧的「说明」展开，悬停可预览全文），
日志默认折叠成一行标题栏（显示最后一行日志摘要），点标题行的箭头、标题文字或摘要
都能展开/收起，执行任务时自动展开。

主窗口导航末尾的「系统」分组依次是「系统日志」与「全局设置」：系统日志集中显示
ffmpeg/AMF 环境自检、全局设置保存与任务调度提示；各功能的执行日志与系统消息互不混在一起。

共有 19 个工具，分为 5 个分组：视频处理（9 个）、音视频提取（2 个）、字幕工具（5 个）、工具箱（2 个）、系统（1 个）。
其中「音频批量转字幕（远程 API）」需要网络与外部字幕服务，地址每次手动填写。

> 单功能窗口中没有「片段删除」页：在「时间线计算器」里点击「填入「片段删除」」时，
> 广告区间会复制到剪贴板，请到完整界面的「片段删除」页粘贴。

### 快捷键

| 按键 | 功能 |
|---|---|
| `↑` `↓` | 在左侧功能列表中切换 |
| `Tab` | 在参数控件间移动 |
| `Ctrl+Q` | 退出 |
| `Ctrl+C` | 停止当前任务 |
| `Ctrl+L` | 清空当前功能页（或系统日志页）的日志 |
| `F1` | 快捷键帮助 |

### 填写路径

路径输入框支持三种填法：

- **拖入**：从资源管理器把文件/文件夹拖到路径框上松手（原生拖放，路径框会高亮）。
  - 目录类字段拖入文件时，自动取其所在文件夹；
  - 一次拖入多个时只取第一个，其余忽略。
- **粘贴**：在路径框内 `Ctrl+V`，会自动剥离引号、还原 `file://` 形式的路径。
- **浏览**：点击右侧「浏览」按钮，在**系统原生文件对话框**中选择
  （目录模式 / 按扩展名过滤的文件模式）。

---

## 命令行（CLI）

不启动界面也能跑全部 18 个功能。参数规格与图形界面**共用同一份声明**（`defs/`），
所以字段名、默认值、取值范围、校验规则完全一致；每个子命令的 `--help` 就是权威说明。

```powershell
python -m video_process.cli --help                 # 源码运行
pip install -e . ; video-process-cli --help        # 安装后可省略 python -m
dist\video-process\视频处理工具集-命令行.exe --help  # 打包后的控制台版
```

### 子命令

| 子命令 | 功能 | 必填参数 |
|---|---|---|
| `merge` | 视频合并 | `--directory` |
| `trim-edges` | 开头结尾裁剪 | `--input-path` |
| `batch-trim` | 批量裁剪（切掉固定时长） | `--directory` |
| `remove-segments` | 片段删除（去广告） | `--input-path` `--segments` |
| `extract-segments` | 片段提取 | `--input-path` `--segments` |
| `split` | 视频分割 | `--input-file` `--split-points` |
| `compress` | 视频压缩 | `--input-path` |
| `convert` | 转换为 MP4 | `--input-path` |
| `volume` | 音量调整（视频不重编码） | `--input-path` |
| `audio` | 音轨提取（转 MP3） | `--input-path` |
| `frame` | 视频截图 | `--input-path` |
| `srt2ass` | SRT 转 ASS | `--input-path` |
| `srt-remove` | SRT 时间段删除 | `--input-file` `--ranges-spec` |
| `jianying-subtitle` | 剪映字幕导出 | `--input-path` |
| `bcut-subtitle` | 必剪字幕导出 | `--input-path` |
| `remote-subtitle` | 音频批量转字幕（远程 API） | `--base-url` `--files` |
| `timeline` | 时间线计算器 | `--text`（可省略，从标准输入读） |
| `zh-convert` | 文本繁简转换 | `--text`（同上） |

参数名是把规格里的字段名换成了 kebab 写法（`output_dir` → `--output-dir`），
布尔开关同时提供 `--xxx` 与 `--no-xxx` 两种写法。

### 全局选项

| 选项 | 说明 |
|---|---|
| `-q` / `--quiet` | 只显示警告与错误 |
| `--overwrite` | 本次允许覆盖已存在的输出（不改写配置文件） |
| `--version` | 查看版本（放在子命令之前） |

默认值来自 `~/.video-process/config.json`（与界面共用）；「全局设置」面板没有对应的子命令，
需要改默认值时，在界面里改或用文本编辑器改这个 JSON。

### 输出与退出码

- 日志、进度、结果摘要 → **stderr**
- 产物路径或纯文本结果 → **stdout**，逐行输出，可直接 `> 结果.txt` 或接管道
- 退出码：`0` 成功、`1` 执行失败、`2` 参数错误
- 运行中 `Ctrl+C` 会终止 ffmpeg 进程树并清理半成品

### 示例

```powershell
# 合并文件夹内所有视频（按文件名排序）
python -m video_process.cli merge D:\剧集\S01

# 去广告：删除两个时间段，AMD 硬件加速，覆盖已有输出
python -m video_process.cli remove-segments D:\剧集\01.mp4 `
    --segments "开头-1:00,5:00-6:00" --mode amd --overwrite

# 每集开头切 15 秒、结尾切 1 分 30 秒，输出到 cut/
python -m video_process.cli batch-trim D:\录播 --start-cut 15 --end-cut 1:30

# 截图与提音轨（静默模式只报错误）
python -m video_process.cli frame D:\a.mp4 --time-position 1:30
python -m video_process.cli audio D:\a.mp4 -q

# 音量整体 +6 dB（只重编码音频，视频画面不重新渲染，处理完原地替换原文件）
python -m video_process.cli volume --input-path D:\a.mp4 --gain-db 6

# 时间线重算：管道进、管道出（结果含广告区间 CSV）
Get-Content .\timeline.txt | python -m video_process.cli timeline --text - > new.txt

# 繁简转换
python -m video_process.cli zh-convert --text "软件和鼠标" --conversion s2twp
```

时间格式（`90` / `1:30` / `1:30:45` / `开头` / `结尾`）与输出命名规则见下文对应章节。

---

## 功能一览

### 视频处理

| 功能 | 说明 |
|---|---|
| 视频合并 | 按文件名排序合并。TS 流快速 / CPU(lix264) / GPU(h264_amf) 三模式 |
| 开头结尾裁剪 | **按绝对时间点**裁剪，保留 `[开头, 结尾)`。快速无损 / AMD 硬件加速 |
| 批量裁剪 | **按要切掉的时长**批量裁剪，默认输出到 `cut/` |
| 片段删除 | 删除多个时间段并合并剩余部分，可自动同步同名 SRT 字幕。快速流复制 / AMD 硬件加速 |
| 片段提取 | 每段输出为独立文件。快速流复制 / AMD 硬件加速 |
| 视频分割 | 按多个时间点分割。快速无损 / 精确重编码 / AMD 加速 + 关键帧对齐 |
| 视频压缩 | AMF 硬件压缩，三档质量预设，递归跳过已压缩文件 |
| 转换 MP4 | 极速换容器 / CPU 重编码 / AMD 重编码 |
| 音量调整 | 只重编码音频，视频画面原样复制（不重新渲染）。dB 增益用「−」「+」按钮按 1 dB 步进，可选削顶保护，处理完原地替换原文件 |

### 音视频提取

| 功能 | 说明 |
|---|---|
| 音轨提取 | 提取全部音轨重编码为 MP3，多音轨自动编号带语言标签 |
| 视频截图 | 指定时间点截取单帧为 JPG，支持批量 |

### 字幕工具

| 功能 | 说明 |
|---|---|
| SRT 转 ASS | 中文字体优化样式（文泉驿正黑、48 号字、带阴影） |
| SRT 时间段删除 | 删除指定时间段并将后续字幕时间整体前移 |
| 剪映字幕导出 | 解析剪映草稿文件 `draft_info.json`，导出文本轨道为 SRT 或 TXT |
| 必剪字幕导出 | 解析必剪草稿文件 `*.bjson`，导出字幕轨道为 SRT 或 TXT |
| 音频批量转字幕（远程 API） | 上传本地音频/视频到 Colab 字幕服务，自动排队、轮询并下载 SRT 到源文件同目录 |

### 工具箱

| 功能 | 说明 |
|---|---|
| 时间线计算器 | 识别 `[广告]` 段落并重算后续时间点，输出广告区间 CSV，可一键填入「片段删除」；直接把 `某集.总结.txt` 拖进输入框，会自动填入同目录同名视频、把广告区间写进「片段删除」，并把结果另存为 `某集.总结.修订.txt` |
| 文本繁简转换 | 粘贴文本即可简繁互转，支持台湾正体（含习惯用语），结果回填输入框 |

### 系统

| 功能 | 说明 |
|---|---|
| 系统日志 | ffmpeg/AMF 环境自检、全局设置保存与任务调度提示 |
| 全局设置 | AMF 默认值、输出策略等，持久化到 `~/.video-process/config.json`（仅界面） |

保存后其余工具面板会立即按新设置填充默认值（面板在切到时才创建，改完设置无需重启）。

---

## 时间格式

| 写法 | 含义 |
|---|---|
| `90` | 90 秒 |
| `1:30` | 1 分 30 秒 |
| `1:30:45` | 1 小时 30 分 45 秒 |
| `1:30:45.500` | 带毫秒 |
| `开头` | 0:00（时间段起始位置） |
| `结尾` | 视频总时长（时间段结尾位置） |

导出时间段用 `-` 连接起止，多个段用 `,` 分隔：`开头-1:00,5:00-结尾`

---

## 输出命名规则

| 工具 | 输出命名 |
|---|---|
| 视频合并 | `{首文件名}_{末文件名}_merge_videos.mp4` |
| 开头结尾裁剪 | `{原名}_trim_edges.mp4` |
| 批量裁剪 | 原名（输出到 `cut/` 子目录） |
| 片段删除 | `{原名}_processed.mp4` |
| 片段提取 | `{原名}_{序号}_{开始}-{结尾}.mp4` |
| 视频分割 | `{原名}_1.ext`、`{原名}_2.ext` … |
| 视频压缩 | `{原名}_compressed{原扩展名}` |
| 转换 MP4 | `{原名}.mp4`（已存在则 `{原名}_converted.mp4`） |
| 音量调整 | 原地替换原文件（保持原名、原目录、原格式，不产生新文件） |
| 音轨提取 | `{原名}.mp3` / `{原名}_track{N}_{语言}.mp3` |
| 视频截图 | `{原名}_time{NN}s.jpg` |
| SRT 转 ASS | `{原名}.ass` |
| SRT 删除 | `{原名}_removed.srt` |
| 剪映字幕导出 | 单轨道 `{草稿名}.srt`，多轨道 `{草稿名}_track{N}.srt` |
| 必剪字幕导出 | 单轨道 `{草稿名}.srt`，多轨道 `{草稿名}_track{N}.srt` |
| 音频批量转字幕 | `{音频/视频原名}.srt`（与源文件同目录，同名直接覆盖）；视频/不支持的音频格式转成的 MP3 只作临时文件，上传后删除 |

### 远程音频转字幕（Colab）

Colab 启动成功后控制台会输出 `https://xxxx.gradio.live`，把它填进面板的「服务地址」即可。
该地址**不会保存**，每次执行都需要重新填写（Colab 重启后地址会变）。

- 音频/视频可多选文件、添加文件夹（递归扫描）或直接拖入。
- wav/flac/mp3/ogg 等直接上传。
- 视频文件会先按「音轨提取（转 MP3）」的参数提取第一条音轨，
  生成的 MP3 与 m4a/wma 等其他服务端无法直接解析的格式一样放在系统临时目录，
  上传成功（或放弃该文件）后立即删除，源文件目录不留任何中间文件。
- 服务端同时最多 5 个排队/处理中任务，程序会自动排队；任务结果保留 1 小时。
- 转换成功后自动下载 SRT，保存为音频同目录的 `{原名}.srt`，同名直接覆盖。
- 面板底部会逐个文件的查询地址：每上传受理成功一条就立即显示一条（不必等整批结束），可一键复制，便于事后核对或手动重查。
- 服务端健康检查发现 CUDA 上下文损坏时会直接中止，并提示重启 Colab runtime。

命令行用法：

```powershell
python -m video_process.cli remote-subtitle `
    --base-url https://xxxx.gradio.live `
    --files "D:\音频\a.mp3" "D:\音频\b.wav" --language Chinese
```

---

## 架构

四层单向依赖，界面与命令行是 `defs/` 的两个平级消费者（互不依赖）：

```
main.py / bundle/entry_gui.py            bundle/entry_cli.py
   ↓                                        ↓
entry.py（GUI 初始化）                  cli.py（argparse，自动生成子命令）
   ↓                                        ↓
ui/app.py（PyQt6 主窗口） ─────────────→ defs/   工具定义：参数规格 + 业务函数（无 UI 依赖）
                                            ↓
tools/                                     纯业务逻辑（无 print / input）
                                            ↓
core/                                      ffmpeg 执行器 / ffprobe 探测 /
                                           时间解析 / 路径处理 / AMF 参数
```

```
video_process/
├── core/        ffmpeg 执行器（流式日志、真实进度、可取消）、ffprobe 探测（带缓存）、
│                时间解析、路径容错与扫描、AMF 参数构造、数据契约
├── tools/       21 个业务逻辑模块，全部通过 ToolContext 回调上报日志与进度
├── defs/        工具定义层：每个工具一份「参数规格 + 业务函数」，不依赖任何 UI 框架
├── ui/          主窗口、通用表单面板、时间线/设置定制面板、
│                路径选择控件、日志与进度控件、QSS 主题、后台任务执行器
├── cli.py      命令行入口：按 defs 的规格自动生成子命令（不 import PyQt6）
├── param_spec.py   声明式参数规格（ParamSpec / ToolDefinition）
├── settings_store.py  全局设置持久化
├── entry.py       统一初始化入口（utf-8 控制台 + ffmpeg PATH 注入）
├── _frozen.py     打包与源码两种运行模式的资源路径适配
```

### 设计要点

- **tools 层不含任何 `print()` / `input()`**，全部通过回调上报，界面与命令行都是消费者
- **定义与渲染分离**：参数规格与业务函数放在 `defs/`，界面只负责渲染
- **声明式表单**：`ParamSpec` 驱动界面控件，新增工具只需一份定义加一个业务函数
- **命令行零重复**：`cli.py` 遍历 `defs.all_tools()` 生成子命令，新增工具自动获得命令行支持
- **异步执行**：ffmpeg 任务跑在 `QThread` 后台线程，通过信号槽（自动 QueuedConnection）
  回到主线程更新进度条与日志，后台线程绝不直接触碰控件；可随时 `Ctrl+C` 停止
- **日志抗洪峰**：长任务可能瞬间产生数千行输出，日志区用 100 ms 定时器批量合并写入，
  并限制保留 2000 行
- **环境自检**：启动时在后台线程检测 ffmpeg/ffprobe 与 AMF 编码器，结果常驻标题栏

---

## 校验脚本

```powershell
python gui_check.py   # 图形界面离屏冒烟：独立日志、系统日志页、单功能窗口
python e2e_check.py   # 真实 ffmpeg 用例 + 远程字幕 mock 用例（自动生成测试素材）
```

两个脚本都会逐项输出 `PASS`/`FAIL`，最后汇总通过与失败数量。
`gui_check.py` 会把 Qt 切到 offscreen 平台，不需要显示器，也不会真正执行视频任务。
无 AMF 编码器的环境会自动跳过 AMD 相关用例。
剪映/必剪字幕导出不涉及 ffmpeg，未包含在端到端测试中。

命令行用例检查：18 个子命令是否齐全、每条参数是否都进了 `--help`、
`timeline` 的结果是否只写 stdout、未知子命令是否返回退出码 2（不依赖 ffmpeg）；
远程字幕用本地 mock HTTP 服务覆盖上传、轮询、下载与同名覆盖。

---

## 打包为可执行程序

用 [PyInstaller](https://pyinstaller.org/) 打成 Windows 可执行程序，双击即用，
**目标机器既不需要安装 Python，也不需要安装 ffmpeg**——二者都随程序一起分发。

```powershell
build.bat                    # 单目录模式（推荐）+ 内置 ffmpeg，图形界面 + 命令行都打
python build.py              # 等价命令
python build.py --onefile    # 单文件模式
python build.py --targets cli   # 只打命令行版（不含 Qt，体积小，适合单独分发）
python build.py --targets gui   # 只打图形界面版
python build.py --clean      # 先清掉上次的构建产物
python build.py --no-ffmpeg  # 不内置 ffmpeg（改用运行机上的）
python build.py --ffmpeg-dir C:\sdk\ffmpeg\bin   # 指定用哪份 ffmpeg
```

首次执行会自动 `pip install pyinstaller`；也可以提前装好：

```powershell
pip install -e ".[build]"
```

### 内置 ffmpeg

构建时自动从 PATH 里找 ffmpeg / ffprobe 并一起打进产物，启动时把它们的目录
挂到 PATH 最前面，因此业务代码不用改，界面自动用上内置副本。

打包的取舍：

- **静态链接版**（gyan.dev / BtbN 的 full build）：只收 `ffmpeg` + `ffprobe`
  两个可执行文件，干净利落
- **动态链接版**：额外把 exe 同目录的 `*.dll`（`avcodec-*.dll` 等）一并收进来，
  漏了程序就起不来
- `ffplay.exe` 用不到，不收

运行时按以下顺序查找，前者优先：

1. 环境变量 `VIDEO_PROCESS_FFMPEG_DIR` 指向的目录（可临时改用别的 ffmpeg）
2. 内置副本（单目录模式在 `_internal/`，单文件模式在临时目录）
3. exe 同目录（自己丢一份 `ffmpeg.exe` 进去即可覆盖，无需重新打包）
4. 系统 PATH

> 顺序 3 意味着：**把 `ffmpeg.exe` / `ffprobe.exe` 复制到 exe 旁边就能换版本**，
> 不必重新打包。界面标题栏的状态芯片会用「内置」/「系统」标明当前生效的来源。

### 产物

默认两个入口一起打（单目录模式输出到 `dist/video-process/`，单文件模式直接输出到 `dist/`）：

| 文件 | 说明 |
|---|---|
| `视频处理工具集.exe` | 图形界面版，双击启动，**不弹控制台黑窗**，带应用图标与版本信息 |
| `视频处理工具集-命令行.exe` | 命令行版，带控制台窗口，`视频处理工具集-命令行.exe --help` 查看用法 |

单目录模式下两个 exe 共用同一份依赖（`_internal/`），所以 CLI 版只是多了一个几 MB 的 exe；
只想分发命令行版时用 `--onefile --targets cli`，产出不含 Qt 的独立单文件（体积小一个数量级）。

命令行版的用法与源码模式完全一致，把 `python -m video_process.cli` 换成 exe 即可：

```powershell
.\视频处理工具集-命令行.exe merge D:\剧集\S01
.\视频处理工具集-命令行.exe remove-segments D:\01.mp4 --segments "开头-1:00,5:00-结尾" --overwrite
```

### 两种模式怎么选

| 模式 | 优点 | 缺点 |
|---|---|---|
| 单目录（默认） | 启动快，升级只替换改动的库 | 分发要拷贝整个目录 |
| `--onefile` | 只有一个 exe，便于传文件 | 每次启动都要解压到临时目录，慢 2~5 秒 |

> 单文件模式把依赖与 ffmpeg 全部内嵌进一个 exe，体积数百 MB，
> 冷启动明显变慢，**建议用单目录模式**。

### 打包相关文件

```
build.py                    构建驱动：检查图标/ffmpeg → 生成版本资源 → 调 spec
build.bat                   Windows 一键打包
bundle/video_process.spec   PyInstaller 配置（GUI/CLI 两个入口 + ffmpeg）
bundle/entry_gui.py         图形界面入口（无控制台）
bundle/entry_cli.py         命令行入口（有控制台）
bundle/ffmpeg_bins.py       定位并收集 ffmpeg / ffprobe 及其动态库
bundle/version_info.py      生成 Windows exe 的版本资源
assets/app.ico              应用图标（自备，缺失时打包仍可正常完成）
video_process/_frozen.py    资源路径 + 内置 ffmpeg 的 PATH 注入
video_process/entry.py      统一初始化入口（utf-8 控制台 + ffmpeg PATH 注入）
video_process/cli.py        命令行实现（按 defs 规格生成子命令）
```

`.github/workflows/build.yml` 在打 tag 或手动触发时跑同一套流程，产出两个压缩包：
`video-process-<版本>-win64.zip`（单目录，GUI + CLI 两个 exe）与
`video-process-cli-<版本>-win64.zip`（单文件，只有命令行版），并作为 Release 附件上传。

### 注意事项

- **体积**：内置 ffmpeg 后单目录产物约 480 MB（ffmpeg 本体就占 385 MB）。
  嫌大可以用 `--ffmpeg-dir` 指定一份精简构建，或 `--no-ffmpeg` 干脆不打包。
  命令行版不含 Qt，`--onefile --targets cli` 的产物就是「ffmpeg + 十几 MB 的 Python 运行时」。
- **位数要一致**：64 位 Python 打出 64 位 exe，32 位系统请用 32 位 Python 打包。
- **建议在干净虚拟环境中打包**，否则容易把无关依赖一起打进去，体积膨胀。
- 界面启动后会像源码模式一样在标题栏检测 ffmpeg 与 AMF 并显示状态芯片。
- 全局设置仍写在 `~/.video-process/config.json`，与源码模式共用一份。
- 杀毒软件对 PyInstaller 产物的误报是行业通病，必要时加白名单或做代码签名。

