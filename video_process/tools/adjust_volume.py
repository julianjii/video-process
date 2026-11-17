# -*- coding: utf-8 -*-
"""调整视频音量：只重编码音频轨，视频画面完全不重新渲染。

为什么快：
    视频包根本不进解码器（-c:v copy），只有音频走 volume 滤镜重编码，
    耗时通常只有全片重编码的几十分之一，画质零损失。

滤镜：
    volume=<N>dB                        N 为正放大，为负衰减
    alimiter=level=0:limit=0.95:latency=1
        可选的削顶保护。必须显式写 level=0：该选项默认开启自动电平，
        会把音频自动归一化回 0dB，直接抵消用户的衰减；
        latency=1 补偿限幅器 lookahead 引入的延迟并冲刷尾部样本，
        避免音画错位与时长变短。

输出策略：原地替换。
    先写同目录同扩展名的临时文件 {原名}.vol_tmp{扩展名}（保持扩展名，
    ffmpeg 才能正确推断容器），校验通过后用 os.replace 覆盖原文件；
    失败、取消或校验不通过时删除临时文件，原文件字节不变。

容器兼容：
    音频编码器按容器选择，避免把 AAC 塞进 webm 这类不支持的容器。
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from ..core.ffmpeg import FFmpegRunner, build_progress_args
from ..core.models import CancelledError, TaskResult, ToolContext, ToolError
from ..core.paths import human_size, remove_files, scan_videos, validate_path
from ..core.probe import clear_cache, get_audio_streams, get_duration

#: 临时文件名标记。同目录写入，保证 os.replace 是同盘原子操作。
TMP_MARKER = ".vol_tmp"

#: 容器 -> 音频编码器。未列出的容器用 AAC。
_AUDIO_ENCODERS = {
    ".webm": "libopus",
    ".avi": "libmp3lame",
    ".wmv": "wmav2",
    ".asf": "wmav2",
}
_DEFAULT_AUDIO_CODEC = "aac"

#: 支持 -movflags +faststart 的容器（其他容器不认识该私有选项）
_FASTSTART_EXTS = {".mp4", ".mov", ".m4v"}

#: MPEG-TS 族：时间戳起点不固定，remux 时归一化到 0
_TS_EXTS = {".ts", ".m2ts", ".mts"}

#: 支持附件（内嵌字体）的容器
_ATTACHMENT_EXTS = {".mkv"}

#: 时长偏差超过该值（秒）时告警
_DURATION_TOLERANCE = 1.0


def _audio_codec_args(ext: str, bitrate: int) -> List[str]:
    """按容器选择音频编码器与码率参数。"""
    codec = _AUDIO_ENCODERS.get(ext.lower(), _DEFAULT_AUDIO_CODEC)
    return ["-c:a", codec, "-b:a", f"{int(bitrate)}k"]


def _build_filter(gain_db: float, limiter: bool) -> str:
    """构造音频滤镜链：纯增益，可选串接限幅器。"""
    text = f"volume={gain_db:g}dB"
    if limiter:
        text += ",alimiter=level=0:limit=0.95:latency=1"
    return text


def _build_cmd(
    input_file: str,
    tmp_file: str,
    gain_db: float,
    bitrate: int,
    limiter: bool,
    duration: Optional[float],
) -> List[str]:
    """组装 ffmpeg 命令：视频等流 copy，音频重编码套滤镜。"""
    ext = os.path.splitext(input_file)[1].lower()

    cmd = [
        "ffmpeg", "-y", "-i", input_file,
        # 显式选流：故意丢弃 data/bin_data/tmcd 等数据流，
        # 否则同容器 remux 容易报 "Could not find tag for codec"
        "-map", "0:v?",
        "-map", "0:a",
        "-map", "0:s?",
    ]
    if ext in _ATTACHMENT_EXTS:
        cmd += ["-map", "0:t?"]   # 保留内嵌字体附件，ASS 字幕才不会丢字形

    # 逐类型指定：视频与字幕流复制，音频重编码。
    # 不用 -c copy 兜底，否则 ffmpeg 会报「同一流指定了多个 -codec 选项」
    cmd += ["-c:v", "copy", "-c:s", "copy", *_audio_codec_args(ext, bitrate)]
    cmd += ["-af", _build_filter(gain_db, limiter)]

    if ext in _FASTSTART_EXTS:
        cmd += ["-movflags", "+faststart"]
    if ext in _TS_EXTS:
        cmd += ["-avoid_negative_ts", "make_zero"]

    if duration:
        cmd += build_progress_args()
    cmd.append(tmp_file)
    return cmd


def _replace_original(ctx: ToolContext, tmp_file: str, target: str) -> None:
    """用临时文件覆盖原文件，处理 Windows 上的短暂文件占用。"""
    try:
        os.replace(tmp_file, target)
    except PermissionError:
        time.sleep(0.5)
        try:
            os.replace(tmp_file, target)
        except PermissionError as exc:
            raise ToolError(
                f"无法覆盖原文件（可能被播放器或其他程序占用）: {target}"
            ) from exc
    ctx.log("  原文件已就地更新")


def _adjust_one(
    runner: FFmpegRunner,
    input_file: str,
    gain_db: float,
    limiter: bool,
    bitrate: int,
) -> bool:
    """处理单个文件（原地替换），返回是否成功。"""
    ctx = runner.ctx
    name = os.path.basename(input_file)

    streams = get_audio_streams(input_file)
    if not streams:
        ctx.error(f"  跳过：文件中没有音轨（原文件未改动）: {name}")
        return False

    duration = get_duration(input_file)
    if duration is None:
        ctx.error(f"  跳过：无法获取视频时长（原文件未改动）: {name}")
        return False

    directory = os.path.dirname(input_file) or "."
    stem, ext = os.path.splitext(name)
    tmp_file = os.path.join(directory, f"{stem}{TMP_MARKER}{ext}")
    remove_files([tmp_file])   # 清理上一轮可能残留的临时文件

    ctx.log(
        f"  音轨 {len(streams)} 条 | 时长 {duration:.2f}s | "
        f"增益 {gain_db:+g} dB"
        + (" | 削顶保护开启" if limiter else "")
    )

    cmd = _build_cmd(input_file, tmp_file, gain_db, bitrate, limiter, duration)
    try:
        runner.run(cmd, duration=duration,
                   description="  处理中（视频不重编码，仅转码音频）...")
    except CancelledError:
        remove_files([tmp_file])
        raise
    except ToolError as exc:
        remove_files([tmp_file])
        ctx.error(f"  失败（原文件未改动）: {exc}")
        return False

    if not os.path.exists(tmp_file) or os.path.getsize(tmp_file) == 0:
        remove_files([tmp_file])
        ctx.error("  失败：未生成有效的临时文件（原文件未改动）")
        return False

    # 临时文件路径可能命中上一轮的探测缓存，先清空再校验
    clear_cache()
    if not get_audio_streams(tmp_file):
        remove_files([tmp_file])
        ctx.error("  失败：输出文件没有音轨（原文件未改动）")
        return False

    out_duration = get_duration(tmp_file)
    if out_duration and abs(out_duration - duration) > _DURATION_TOLERANCE:
        ctx.warn(
            f"  注意：输出时长 {out_duration:.2f}s 与原时长 {duration:.2f}s 偏差较大"
        )

    src_size = os.path.getsize(input_file)
    new_size = os.path.getsize(tmp_file)
    try:
        _replace_original(ctx, tmp_file, input_file)
    except ToolError as exc:
        remove_files([tmp_file])
        ctx.error(f"  {exc}（原文件未改动）")
        return False

    clear_cache()   # 原文件内容已变，丢弃旧探测结果
    ratio = (new_size - src_size) / src_size * 100 if src_size else 0.0
    ctx.success(
        f"  完成: {name}  {human_size(src_size)} -> {human_size(new_size)} "
        f"({ratio:+.1f}%)"
    )
    return True


def adjust_volume(
    input_path: str,
    gain_db: float = 6.0,
    limiter: bool = True,
    audio_bitrate: int = 192,
    recursive: bool = True,
    ctx: Optional[ToolContext] = None,
) -> TaskResult:
    """调整视频音量，只重编码音频，视频画面不重新渲染。

    参数:
        input_path:    输入视频文件或文件夹
        gain_db:       音量增益（分贝），正数放大、负数衰减
        limiter:       是否启用削顶保护（alimiter 限幅器）
        audio_bitrate: 重编码音频的目标码率（kbps）
        recursive:     文件夹模式是否递归子目录

    说明:
        处理完成后原地替换原文件；失败或取消时原文件不受影响。
    """
    ctx = ctx or ToolContext()
    runner = FFmpegRunner(ctx)

    ok, resolved, err = validate_path(input_path)
    if not ok:
        return TaskResult(success=False, message=err)

    try:
        gain = float(gain_db)
    except (TypeError, ValueError):
        return TaskResult(success=False, message=f"无效的音量增益: {gain_db}")

    if abs(gain) < 1e-6:
        return TaskResult(success=False, message="音量增益为 0 dB，无需调整")
    if not -60.0 <= gain <= 60.0:
        return TaskResult(success=False, message="音量增益超出范围（-60 ~ +60 dB）")

    try:
        bitrate = int(audio_bitrate)
    except (TypeError, ValueError):
        return TaskResult(success=False, message=f"无效的音频码率: {audio_bitrate}")
    if bitrate <= 0:
        return TaskResult(success=False, message="音频码率必须大于 0")

    ctx.log(f"音量调整: {gain:+g} dB（音频 {bitrate} kbps"
            + ("，含削顶保护）" if limiter else "）"))
    ctx.log("视频画面不重编码（-c copy），处理完成后原地替换原文件")

    outputs: List[str] = []
    success = 0
    failed = 0

    if os.path.isfile(resolved):
        ctx.log(f"处理: {os.path.basename(resolved)}")
        ok_one = _adjust_one(runner, resolved, gain, limiter, bitrate)
        if ok_one:
            outputs.append(resolved)
            success += 1
        else:
            failed += 1
    else:
        files = scan_videos(resolved, recursive=recursive,
                            exclude_contains=TMP_MARKER)
        if not files:
            return TaskResult(success=False,
                              message=f"文件夹中没有找到视频文件: {resolved}")
        ctx.log(f"批量模式（递归={recursive}）：找到 {len(files)} 个视频")

        for i, f in enumerate(files, 1):
            ctx.check_cancel()
            rel = os.path.relpath(f, resolved)
            ctx.log(f"[{i}/{len(files)}] {rel}")
            ctx.progress(percent=i / len(files) * 100, file=rel)
            if _adjust_one(runner, f, gain, limiter, bitrate):
                outputs.append(f)
                success += 1
            else:
                failed += 1

    return TaskResult(
        success=failed == 0 and success > 0,
        message=f"完成，成功 {success} 个，失败 {failed} 个",
        outputs=outputs,
    )
