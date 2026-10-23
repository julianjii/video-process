---
name: video-process-cli
overview: 复用 defs/ 的声明式参数规格，新增单一命令行入口（子命令 = 各功能），补齐控制台版 exe 打包，更新 README 并生成项目 skill。
todos:
  - id: cli-core
    content: 新增 video_process/cli.py：由 defs 自动生成 16 个子命令，含类型映射、设置默认值、校验与 stdin
    status: pending
  - id: cli-run
    content: 实现 CLI 执行与输出契约：ToolContext、SIGINT 取消、stdout/stderr 分流、退出码
    status: pending
    dependencies:
      - cli-core
  - id: cli-package
    content: 新增 bundle/entry_cli.py，改 spec 与 build.py 同时产出控制台版 exe
    status: pending
    dependencies:
      - cli-core
      - cli-run
  - id: cli-docs
    content: pyproject 增加 video-process-cli 入口；README 新增 CLI 章节并同步打包章节
    status: pending
    dependencies:
      - cli-package
  - id: cli-skill
    content: 用 [skill:skill-creator] 在 .codebuddy/skills/video-process-cli/ 生成 SKILL.md 命令速查
    status: pending
    dependencies:
      - cli-docs
  - id: cli-verify
    content: 扩展 e2e_check.py CLI 自检，验证全部子命令 --help 与 timeline 用例通过
    status: pending
    dependencies:
      - cli-run
      - cli-docs
---

## 用户需求
为现有视频处理工具集补齐命令行（CLI）版本：每个 GUI 功能都能在终端直接调用，参数语义与图形界面保持一致；把用法写入 README，并生成一个可复用的 CodeBuddy 技能包。

## 产品概述
在保留 PyQt6 图形界面的前提下，新增一个统一命令行入口，按功能分组暴露全部可执行工具。用户在终端一条命令即可完成合并、裁剪、去广告、提取、压缩、转码、音轨/截图、字幕转换与导写、时间线重算、繁简转换等操作；也可被脚本/批处理循环调用。打包产物除现有 GUI exe 外，额外产出一个可双击或在终端运行的控制台版 exe。

## 核心功能
- 单一入口 + 子命令：`python -m video_process.cli TOOL_ID [选项]`，`--help` 列出全部功能，每个功能 `--help` 给出参数、默认值与取值说明
- 参数与 GUI 完全对齐：同名字段、同默认值、同取值范围、同校验规则；读取 `~/.video-process/config.json` 中的全局默认值
- 全局开关：静默模式（仅告警/错误）、临时覆盖已存在输出、版本号
- 输出约定：日志与结果摘要走 stderr，产物路径或纯文本结果走 stdout，可直接管道重定向；成功 0 / 失败 1 / 参数错误 2
- 文本类功能支持从标准输入读取，便于 `timeline`、`zh-convert` 参与管道
- 运行中按 Ctrl+C 可干净取消（杀掉 ffmpeg 进程树），不留孤儿进程
- 打包：单目录/单文件两种模式均同时产出 GUI exe 与控制台 exe，后者内置 ffmpeg，脱离 Python 环境可用
- 文档：README 新增 CLI 章节（入口、16 个功能一览、全局选项、输出与退出码、示例、打包用法），并同步打包章节的产物与文件清单
- 技能包：项目级 CodeBuddy 技能，描述何时使用与完整命令速查，供后续会话直接调用


## 技术栈
- 语言/运行时：Python 3.9+（现状），零新增第三方依赖
- 参数解析：标准库 `argparse`（含 `BooleanOptionalAction`）
- 业务复用：直接调用 `video_process/defs` 的 `ToolDefinition.resolve_runner()`，不复制任何 ffmpeg 逻辑
- 打包：沿用 PyInstaller + 现有 spec，新增控制台入口
- 架构约束：CLI 不 import PyQt6，`tools/`、`core/`、`ui/`、`defs/` 行为零改动

## 实现方案
核心策略是「不手写 16 份参数表」。`defs/` 已是与 UI 无关的唯一事实来源（`ParamSpec` + 业务函数），UI 与 CLI 都只是它的两个消费者：

- 遍历 `defs.all_tools()`，为每个 `resolve_runner() is not None` 的定义生成子命令（跳过纯界面面板 `settings`）
- `ParamSpec.kind` 映射到 argparse：`int/float` → 类型转换；`choice` → `choices=[实际值]`（全为 int 时 `type=int`，兼容 merge/convert 的 1/2/3）；`bool` → `BooleanOptionalAction`（默认值不动）；其余 → `str`。参数名 kebab（`--output-dir`），回填字典仍用 snake_case 的 `spec.name`
- 默认值复刻 UI 规则：`spec.setting` 有值且设置项不为 None 时用设置值，否则用 `spec.default`（`ui/panels/base.py:190-201` 同款语义）
- 校验复刻 UI 规则：只校验 `spec.should_show(values)` 为真的参数，失败走 `parser.error()`
- 执行与 `ui/tasks.py:75-79` 完全一致：`runner(ctx=ctx, **values)`，捕获 `CancelledError/ToolError/Exception` 转为失败结果
- 取消：SIGINT 只置位 `threading.Event`，由现有 `FFmpegRunner` 的 0.1s 轮询完成 taskkill 进程树
- 输出：日志用 `ToolContext.on_log` 打到 stderr 并按级别着色/过滤，进度只在 stderr 是终端时用 `\r` 刷新；`TaskResult.outputs` 逐行写 stdout（文件工具得到产物路径，timeline/zh-convert 得到纯文本），`warnings` 写 stderr
- 打包：spec 内做两次 `Analysis`（GUI 入口、CLI 入口），单目录模式两 exe 共用 GUI 的 binaries 与 COLLECT 目录（GUI 依赖是 CLI 的超集），单文件模式各用各的（CLI 产物不含 Qt，体积更小）

性能与代价：解析器构建 O(工具数)，一次进程只做一次，可忽略；打包时间约翻倍（两次依赖分析），这是产出两个 exe 的必然代价，已用共享 binaries 避免单目录产物体积翻倍。

## 实现要点与风险控制
- **零行为回归**：只新增文件与打包/文档改动，不改任何 tools/core/defs 代码；CLI 只是第二个消费者
- **设置语义一致**：`amf_bitrate` 等设置值为 None 时必须保留 spec 默认值，否则「留空=自动」语义被顶掉
- **Windows 控制台**：入口先执行 `ensure_utf8_stdio()`，再 `prepare_ffmpeg_path()`（打包后 ffmpeg 靠 PATH 注入，顺序不能反）
- **不引入 PyQt**：`defs` 与 `tools` 已与 UI 解耦，CLI 入口切勿间接引入 `ui`，否则单文件 CLI 会拖进整个 Qt
- **overwrite 默认关闭**：工具遇已存在输出会失败并提示「可在设置中开启覆盖」，CLI 用 `--overwrite` 临时置 `Settings.overwrite`，不改写配置文件
- **参数冲突**：全局 `--quiet`、`--overwrite`、`--version` 需挂在各子命令上（子命令之后才解析），并确认与现有 spec 名无撞名
- **多行文本**：`multiline` 参数不设为必填，省略且 stdin 非 tty 或显式传 `-` 时读 stdin，支持 `cat tl.txt | ... timeline`
- **安全**：不拼接 shell 字符串，路径原样传给既有 `validate_path`，沿用现有校验与错误处理

## 目录结构
```
video-process/
├── video_process/
│   ├── cli.py                    # [NEW] 唯一 CLI 入口。ensure_utf8_stdio + prepare_ffmpeg_path；
│   │                             #   遍历 defs.all_tools() 生成子命令（跳过 runner 为 None 的 settings）；
│   │                             #   ParamSpec→argparse 映射、设置默认值、should_show 校验、stdin 兜底；
│   │                             #   ToolContext(日志/进度/取消)、SIGINT→threading.Event、
│   │                             #   stdout=outputs、stderr=日志与摘要、退出码 0/1/2；不 import PyQt6
│   └── __init__.py               # [UNCHANGED] 已有 __version__
├── bundle/
│   ├── entry_cli.py              # [NEW] 控制台入口薄封装：from video_process.cli import main
│   └── video_process.spec        # [MODIFY] 新增 VP_CLI_NAME（默认「视频处理工具集-命令行」）；
│                                 #   第二次 Analysis(entry_cli.py)；console=True 的第二个 EXE；
│                                 #   onedir 时 COLLECT(exe_gui, exe_cli, a_gui.binaries, ...)，
│                                 #   onefile 时 CLI 用 a_cli.binaries（不含 Qt）
├── build.py                      # [MODIFY] 透传 VP_CLI_NAME；clean() 覆盖两个 exe；
│                                 #   report() 打印两个产物路径与一句 CLI 用法示例
├── pyproject.toml                # [MODIFY] [project.scripts] video-process-cli = "video_process.cli:main"
├── README.md                     # [MODIFY] 新增「命令行（CLI）」章节：三种入口写法、16 个子命令表
│                                 #   （id/功能/必填参数）、全局选项、stdout/stderr 约定、退出码、
│                                 #   时间格式与典型示例、配置文件说明；同步「产物」表与「打包相关文件」清单
├── e2e_check.py                  # [MODIFY] 追加 CLI 自检：逐个构建子命令 parser 不报错 +
│                                 #   timeline 纯文本用例（不依赖 ffmpeg）
└── .codebuddy/
    └── skills/
        └── video-process-cli/
            └── SKILL.md          # [NEW] YAML frontmatter（name/description，第三人称说明触发场景）
                                  #   正文：16 个子命令速查表、必填参数、全局选项、时间格式、
                                  #   输出与退出码、常见组合示例、打包 exe 调用方式、配置文件路径
```

## 关键约定（实现时的接口契约）
- 子命令名即 `ToolDefinition.id`：merge、trim-edges、batch-trim、remove-segments、extract-segments、split、compress、convert、audio、frame、srt2ass、srt-remove、jianying-subtitle、bcut-subtitle、timeline、zh-convert（settings 不提供子命令）
- `main(argv: Optional[Sequence[str]] = None) -> int`，模块底部 `if __name__ == "__main__": raise SystemExit(main())`，使 `python -m video_process.cli` 与安装后的 `video-process-cli` 共用同一实现
- 无子命令时打印总帮助并返回 0；未知子命令由 argparse 报错返回 2


## Agent Extensions
### Skill
- **skill-creator**
  - Purpose: 按 CodeBuddy 技能规范创建项目级技能骨架并校验 frontmatter、命名与目录结构（生成后删除示例 scripts/references/assets，只留 SKILL.md）
  - Expected outcome: `.codebuddy/skills/video-process-cli/SKILL.md` 结构合法、`name` 与目录同名、`description` 明确触发场景，正文为项目 CLI 的完整命令速查，可直接被后续会话加载使用
