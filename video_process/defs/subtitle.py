# -*- coding: utf-8 -*-
"""字幕类工具定义。"""

from __future__ import annotations

from ..core.paths import (
    AUDIO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from ..param_spec import (
    ParamSpec,
    ToolDefinition,
    validate_http_url,
    validate_positive_int,
    validate_segments,
)
from ..tools.remote_subtitle import (
    LANGUAGE_CHOICES as REMOTE_LANGUAGE_CHOICES,
    transcribe_audio_batch,
)
from ..tools.bcut_subtitle import (
    FORMAT_CHOICES as BCUT_FORMAT_CHOICES,
    export_bcut_subtitles,
)
from ..tools.jianying_subtitle import (
    FORMAT_CHOICES as JIANYING_FORMAT_CHOICES,
    TIME_UNIT_CHOICES as JIANYING_TIME_UNIT_CHOICES,
    export_jianying_subtitles,
)
from ..tools.remove_srt import remove_srt_segments
from ..tools.srt_to_ass import srt_to_ass

GROUP = "字幕工具"

SUBTITLE_EXTS = list(SUBTITLE_EXTENSIONS)
AUDIO_EXTS = list(AUDIO_EXTENSIONS)
MEDIA_EXTS = list(AUDIO_EXTENSIONS) + list(VIDEO_EXTENSIONS)

SRT2ASS = ToolDefinition(
    id="srt2ass",
    title="SRT 转 ASS",
    group=GROUP,
    description=(
        "将 SRT 字幕转换为样式更丰富的 ASS 格式。\n"
        "默认样式针对中文字体优化（文泉驿正黑、48 号字、描边 3、阴影 3），"
        "适合视频压制。\n"
        "输出: {原名}.ass"
    ),
    specs=[
        ParamSpec(
            name="input_path",
            label="SRT 文件或文件夹",
            kind="path",
            file_patterns=SUBTITLE_EXTS,
            help="支持单个 .srt 文件或整个文件夹批量处理",
        ),
        ParamSpec(
            name="output_dir",
            label="输出文件夹（可选）",
            kind="outdir",
            default="",
            required=False,
            help="留空=输出到输入同目录",
        ),
        ParamSpec(
            name="fontname",
            label="字体名",
            kind="text",
            default="文泉驿正黑",
            required=False,
        ),
        ParamSpec(
            name="fontsize",
            label="字号",
            kind="int",
            default=48,
            required=False,
            validator=validate_positive_int,
        ),
    ],
    runner=srt_to_ass,
)

SRT_REMOVE = ToolDefinition(
    id="srt-remove",
    title="SRT 时间段删除",
    group=GROUP,
    description=(
        "删除 SRT 字幕的指定时间段，并将后续字幕时间整体前移。\n"
        "示例: 1:00-2:00 或 1:00-2:00,5:00-6:00\n"
        "时间支持 SRT 标准格式 HH:MM:SS,mmm。\n"
        "输出: {原名}_removed.srt"
    ),
    specs=[
        ParamSpec(
            name="input_file",
            label="字幕文件",
            kind="file",
            file_patterns=[".srt", ".txt"],
            help="支持 .srt 与 .srt.txt",
        ),
        ParamSpec(
            name="ranges_spec",
            label="要删除的时间段",
            kind="text",
            default="",
            validator=validate_segments,
            help="格式 开始-结尾,开始-结尾",
        ),
        ParamSpec(
            name="output_file",
            label="输出文件（可选）",
            kind="text",
            default="",
            required=False,
            help="留空=自动生成 {原名}_removed.srt",
        ),
    ],
    runner=remove_srt_segments,
)

JIANYING = ToolDefinition(
    id="jianying-subtitle",
    title="剪映字幕导出（SRT/TXT）",
    group=GROUP,
    description=(
        "解析剪映草稿文件 draft_info.json，导出文本轨道为 SRT 或 TXT。\n"
        "  · SRT：含时间轴，序号按每条轨道从 1 重新编号\n"
        "  · TXT：仅台词，按时间顺序逐条排列\n"
        "每条文本轨道单独导出一个文件；单轨道输出 {草稿名}.srt，"
        "多轨道输出 {草稿名}_track{N}.srt。\n"
        "输入支持 draft_info.json、单个草稿目录或整个草稿库根目录。"
    ),
    specs=[
        ParamSpec(
            name="input_path",
            label="剪映草稿文件或文件夹",
            kind="path",
            file_patterns=[".json"],
            help="draft_info.json、草稿目录或草稿库根目录（自动向下查找）",
        ),
        ParamSpec(
            name="export_format",
            label="导出格式",
            kind="choice",
            default="srt",
            choices=JIANYING_FORMAT_CHOICES,
        ),
        ParamSpec(
            name="time_unit",
            label="时间单位",
            kind="choice",
            default="auto",
            choices=JIANYING_TIME_UNIT_CHOICES,
            required=False,
            help="时间轴全部错位时，改选毫秒重试",
        ),
        ParamSpec(
            name="recursive",
            label="递归子目录",
            kind="bool",
            default=True,
            required=False,
            help="输入为草稿库根目录时生效",
            setting="recursive_scan",
        ),
        ParamSpec(
            name="output_dir",
            label="输出文件夹（可选）",
            kind="outdir",
            default="",
            required=False,
            help="留空=输出到草稿文件同目录",
        ),
    ],
    runner=export_jianying_subtitles,
)

BCUT = ToolDefinition(
    id="bcut-subtitle",
    title="必剪字幕导出（SRT/TXT）",
    group=GROUP,
    description=(
        "解析必剪（Bcut）草稿文件 *.bjson，导出字幕轨道为 SRT 或 TXT。\n"
        "  · SRT：含时间轴，序号按每条轨道从 1 重新编号\n"
        "  · TXT：仅台词，按时间顺序逐条排列\n"
        "每条字幕轨道单独导出一个文件；单轨道输出 {草稿名}.srt，"
        "多轨道输出 {草稿名}_track{N}.srt。\n"
        "输入支持 .bjson 文件、单个草稿目录或整个草稿库根目录"
        "（默认 ~/Documents/Bcut Drafts）。"
    ),
    specs=[
        ParamSpec(
            name="input_path",
            label="必剪草稿文件或文件夹",
            kind="path",
            file_patterns=[".bjson"],
            help="*.bjson、草稿目录或草稿库根目录（自动向下查找，取最新的 bjson）",
        ),
        ParamSpec(
            name="export_format",
            label="导出格式",
            kind="choice",
            default="srt",
            choices=BCUT_FORMAT_CHOICES,
        ),
        ParamSpec(
            name="recursive",
            label="递归子目录",
            kind="bool",
            default=True,
            required=False,
            help="输入为草稿库根目录时生效",
            setting="recursive_scan",
        ),
        ParamSpec(
            name="output_dir",
            label="输出文件夹（可选）",
            kind="outdir",
            default="",
            required=False,
            help="留空=输出到草稿文件同目录",
        ),
    ],
    runner=export_bcut_subtitles,
)

REMOTE_SUBTITLE = ToolDefinition(
    id="remote-subtitle",
    title="音频批量转字幕（远程 API）",
    group=GROUP,
    description=(
        "上传本地音频到远程字幕服务（Colab），自动排队、轮询并下载 SRT。\n"
        "服务地址每次手动填写，例如 https://xxxx.gradio.live"
        "（Colab 启动成功后控制台输出的地址）。\n"
        "字幕保存到音频所在文件夹：{音频原名}.srt，同名直接覆盖。\n"
        "视频会先提取音轨为 MP3（保存到视频同目录）；m4a/wma 等"
        "服务端无法直接解析的格式会转成临时 MP3 再上传。\n"
        "服务端同时最多 5 个排队/处理中任务，程序会自动排队；结果保留 1 小时。"
    ),
    specs=[
        ParamSpec(
            name="base_url",
            label="服务地址（BASE_URL）",
            kind="text",
            default="",
            help=(
                "每次手动填写，如 https://xxxx.gradio.live"
                "（末尾不用带 /api）"
            ),
            validator=validate_http_url,
        ),
        ParamSpec(
            name="files",
            label="音频/视频文件",
            kind="files",
            default=(),
            file_patterns=MEDIA_EXTS,
            help=(
                "可多选文件、添加文件夹（递归扫描音频与视频），或直接拖入；"
                "mp4/m4a 等格式会先转成 FLAC 再上传"
            ),
        ),
        ParamSpec(
            name="language",
            label="音频语种",
            kind="choice",
            default="Chinese",
            choices=REMOTE_LANGUAGE_CHOICES,
            required=False,
        ),
    ],
    runner=transcribe_audio_batch,
    panel_class="remote-subtitle",
)

DEFS = [SRT2ASS, SRT_REMOVE, JIANYING, BCUT, REMOTE_SUBTITLE]

__all__ = ["GROUP", "DEFS"]
