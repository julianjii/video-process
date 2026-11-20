# -*- coding: utf-8 -*-
"""远程音频转字幕：上传音频 → 轮询任务 → 下载 SRT 到音频同目录。

服务端接口（Colab 启动后由控制台输出 BASE_URL）：
    POST {BASE_URL}/api/subtitles/tasks         multipart: file + language
    GET  {BASE_URL}/api/subtitles/tasks/{id}    查询状态
    GET  {BASE_URL}/api/subtitles/tasks/{id}/srt  下载字幕
    GET  {BASE_URL}/healthz                     健康检查

服务端同时最多允许 5 个排队/处理中的任务，本模块串行上传、并行轮询，
名额空出后再上传下一个文件；单任务结果 1 小时后会被清理。

视频文件会先用 ffmpeg 提取音轨为 MP3（保存到视频同目录，参数与
「音轨提取（转 MP3）」一致），再上传该 MP3。

网络层只用标准库（urllib），不引入 requests 等额外依赖。
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import ssl
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ..core.ffmpeg import FFmpegRunner, build_progress_args
from ..core.models import CancelledError, TaskResult, ToolContext, ToolError
from ..core.paths import is_video_file, scan_media, validate_path
from ..core.probe import get_audio_streams, get_duration

#: 服务端队列/处理中任务上限
MAX_ACTIVE_TASKS = 5
#: 轮询间隔（秒）
POLL_INTERVAL = 2.0
#: 单个任务最长等待时间（秒）——服务端结果本身只保留 1 小时
TASK_TIMEOUT = 3600.0
#: 单任务连续网络错误次数上限（轮询）
MAX_POLL_ERRORS = 10
#: 上传/下载/普通请求超时（秒）
UPLOAD_TIMEOUT = 300.0
REQUEST_TIMEOUT = 30.0
#: 队列满时的退避时间（秒）
QUEUE_FULL_BACKOFF = 5.0
#: 字幕下载重试次数
DOWNLOAD_RETRIES = 3
#: 服务端 librosa/soundfile 可直接读取的格式（视频与其余格式先转 MP3）
DIRECT_AUDIO_EXTENSIONS = (
    ".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff", ".au",
)

#: 界面/命令行共用的语种选项
LANGUAGE_CHOICES = [
    ("中文", "Chinese"),
    ("英语", "English"),
    ("日语", "Japanese"),
    ("韩语", "Korean"),
    ("法语", "French"),
    ("德语", "German"),
]

_SSL_CONTEXT: Optional[ssl.SSLContext] = None


# ---------------------------------------------------------------------------
# HTTP 基础设施
# ---------------------------------------------------------------------------
class _HttpError(Exception):
    """服务端返回了非 2xx 状态码。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class _NetworkError(Exception):
    """连接失败、超时或响应无法解析。"""


def _ssl_context() -> ssl.SSLContext:
    """优先用 certifi 的 CA 包，缺失时回退到系统证书库。"""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        try:
            import certifi  # type: ignore

            _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # noqa: BLE001 - 没有 certifi 或读取失败都回退
            _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def _detail_from_body(body: bytes) -> str:
    """从错误响应体里提取 detail 字段（FastAPI 风格）。"""
    text = body.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(data, dict) and data.get("detail"):
        return str(data["detail"])
    return text[:300]


def _request(
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[dict] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> Tuple[int, bytes]:
    """发一个 HTTP 请求，返回 (状态码, 响应体)。非 2xx 抛 _HttpError。"""
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context()
        ) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - 读不到响应体时只用状态码
            body = b""
        detail = _detail_from_body(body) or str(exc.reason or "请求失败")
        raise _HttpError(int(exc.code), detail) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise _NetworkError(f"无法连接服务: {reason}") from exc
    except (OSError, ValueError) as exc:
        raise _NetworkError(f"请求失败: {exc}") from exc


def normalize_base_url(raw: str) -> str:
    """规范化服务地址：去空白/引号/末尾斜杠，省略协议时补 https。"""
    text = str(raw or "").strip().strip('"').strip("'").rstrip("/")
    if not text:
        raise ToolError("服务地址不能为空")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ToolError("服务地址格式不正确，例如 https://xxxx.gradio.live")
    return text


def _absolute_url(base_url: str, url: str) -> str:
    """把服务端返回的相对路径拼成绝对地址。"""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url
    return base_url + ("" if url.startswith("/") else "/") + url


def _multipart_body(audio_path: str, language: str) -> Tuple[bytes, str]:
    """构造 multipart/form-data 请求体与 boundary。"""
    boundary = "----VideoProcess" + uuid.uuid4().hex
    filename = (
        os.path.basename(audio_path)
        .replace("\r", "")
        .replace("\n", "")
        .replace('"', "_")
    )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"{language}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")

    with open(audio_path, "rb") as handle:
        content = handle.read()
    return head + content + tail, boundary


def _sleep(ctx: ToolContext, seconds: float) -> None:
    """可取消的 sleep：0.2 秒切片，保证取消请求能及时响应。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        ctx.check_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------
@dataclass
class _TaskState:
    audio_path: str
    filename: str
    task_id: str
    status_url: str
    created_at: float = field(default_factory=time.monotonic)
    status: str = "queued"
    progress: str = ""
    last_log_status: str = ""
    last_log_progress: str = ""
    last_log_at: float = 0.0
    network_errors: int = 0
    last_error_log_at: float = 0.0

    def should_log(self, status: str, progress: str) -> bool:
        """状态/进度文本变化且距上次输出至少 3 秒时才记录，避免刷屏。"""
        now = time.monotonic()
        if status == self.last_log_status and progress == self.last_log_progress:
            return False
        if now - self.last_log_at < 3.0:
            return False
        self.last_log_status = status
        self.last_log_progress = progress
        self.last_log_at = now
        return True

    def warn_error(self, ctx: ToolContext, message: str) -> None:
        """网络错误日志限频：同一任务 10 秒内最多提示一次。"""
        now = time.monotonic()
        if now - self.last_error_log_at < 10.0:
            return
        self.last_error_log_at = now
        ctx.warn(message)


def _percent(done: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return done / total * 100.0


# ---------------------------------------------------------------------------
# 本地文件整理
# ---------------------------------------------------------------------------
def _needs_transcode(path: str) -> bool:
    """视频一律提取音轨；音频中服务端读不了的格式先转 MP3。"""
    return is_video_file(path) or (
        os.path.splitext(path)[1].lower()
        not in DIRECT_AUDIO_EXTENSIONS
    )


def _extract_audio_to_mp3(
    ctx: ToolContext,
    source_path: str,
    target_path: str,
    index: int,
    total: int,
) -> None:
    """提取第一条音轨并编码为 MP3（参数与「音轨提取（转 MP3）」一致）。"""
    streams = get_audio_streams(source_path)
    if not streams:
        raise ToolError("文件中没有找到音轨，无法转字幕")
    if len(streams) > 1:
        language = streams[0].get("language") or "und"
        ctx.warn(
            f"  文件含 {len(streams)} 条音轨，取第 1 条（{language}）"
        )

    duration = get_duration(source_path)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", source_path,
        "-map", "0:a:0",
        "-vn",
        "-c:a", "libmp3lame",
        "-q:a", "9",
        "-map_metadata", "0",
        "-id3v2_version", "3",
    ]
    if duration:
        cmd += build_progress_args()
    cmd.append(target_path)

    FFmpegRunner(ctx).run(
        cmd,
        duration=duration,
        description=f"  提取音轨 {index}/{total}…",
    )


def _prepare_for_upload(
    ctx: ToolContext, path: str, index: int, total: int
) -> Tuple[str, str]:
    """返回 (实际上传文件, 需要清理的临时目录)。

    视频：提取 MP3 保存到视频同目录（与「音轨提取」一致），永久保留。
    其余服务端不支持的音频格式：转成临时 MP3，上传后删除。
    """
    if not _needs_transcode(path):
        return path, ""

    name = os.path.basename(path)

    if is_video_file(path):
        target = os.path.splitext(path)[0] + ".mp3"
        temp_target = target + ".part.mp3"
        ctx.log(f"  {name}: 视频文件，先提取音轨为 MP3（保存到视频同目录）…")
        try:
            _extract_audio_to_mp3(ctx, path, temp_target, index, total)
            os.replace(temp_target, target)
        except BaseException:
            try:
                if os.path.exists(temp_target):
                    os.remove(temp_target)
            except OSError:
                pass
            raise
        ctx.success(f"  音轨已保存: {target}")
        return target, ""

    ext = os.path.splitext(path)[1].lower() or "(无扩展名)"
    ctx.log(
        f"  {name}: 格式 {ext} 服务端无法直接解析，先转换为临时 MP3…"
    )
    temp_dir = tempfile.mkdtemp(prefix="vp-remote-subtitle-")
    target = os.path.join(temp_dir, f"audio_{index}.mp3")
    try:
        _extract_audio_to_mp3(ctx, path, target, index, total)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return target, temp_dir


def _collect_audio_files(raw_files) -> Tuple[List[str], List[str]]:
    """把界面/命令行传入的路径展开为音频文件列表，返回 (文件, 错误)。"""
    if raw_files is None:
        items: List[str] = []
    elif isinstance(raw_files, (list, tuple, set)):
        items = [str(item) for item in raw_files]
    else:
        items = str(raw_files).replace("\r\n", "\n").replace("\r", "\n").split("\n")

    files: List[str] = []
    errors: List[str] = []
    seen = set()

    def add_file(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            errors.append(f"无法读取文件信息: {path}（{exc}）")
            return
        if size <= 0:
            errors.append(f"文件为空（0 字节）: {os.path.basename(path)}")
            return
        seen.add(key)
        files.append(os.path.abspath(path))

    for raw in items:
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        ok, resolved, err = validate_path(candidate, kind="any")
        if not ok:
            errors.append(err)
            continue
        if os.path.isdir(resolved):
            found = scan_media(resolved, recursive=True)
            if not found:
                errors.append(f"文件夹中没有找到音频/视频文件: {resolved}")
                continue
            for path in found:
                add_file(path)
        else:
            add_file(resolved)

    return files, errors


# ---------------------------------------------------------------------------
# 服务端交互
# ---------------------------------------------------------------------------
def _check_health(ctx: ToolContext, base_url: str) -> None:
    """健康检查：CUDA 已损坏时直接中止，其余失败只警告。"""
    try:
        _status, body = _request("GET", base_url + "/healthz", timeout=10.0)
    except _HttpError as exc:
        ctx.warn(
            f"健康检查失败（HTTP {exc.status}），仍尝试上传: {exc.detail}"
        )
        return
    except _NetworkError as exc:
        ctx.warn(f"健康检查失败，仍尝试上传: {exc}")
        return

    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        ctx.warn("健康检查返回内容不是 JSON，已跳过")
        return

    if data.get("cuda_corrupted"):
        reason = str(data.get("reason") or "").strip()
        message = (
            "服务当前不可用：检测到不可恢复的 CUDA 错误，请重启 Colab runtime。"
        )
        if reason:
            message += f" 原始错误: {reason}"
        raise ToolError(message)

    ctx.log(
        "服务健康检查通过（status={status}，活动任务={active}）".format(
            status=data.get("status", "?"),
            active=data.get("active_tasks", "?"),
        )
    )


def _create_remote_task(
    base_url: str, audio_path: str, language: str
) -> Tuple[str, str]:
    """上传一个音频，返回 (task_id, status_url)。"""
    body, boundary = _multipart_body(audio_path, language)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
    }
    _status, response = _request(
        "POST",
        base_url + "/api/subtitles/tasks",
        data=body,
        headers=headers,
        timeout=UPLOAD_TIMEOUT,
    )
    try:
        data = json.loads(response.decode("utf-8", "replace"))
    except ValueError as exc:
        raise _NetworkError(
            f"上传响应不是有效 JSON: {response[:200]!r}"
        ) from exc

    task_id = str(data.get("task_id") or "")
    status_url = _absolute_url(base_url, str(data.get("status_url") or ""))
    if not status_url:
        status_path = str(data.get("status_path") or "")
        if status_path:
            status_url = _absolute_url(base_url, status_path)
        elif task_id:
            status_url = f"{base_url}/api/subtitles/tasks/{task_id}"
    if not task_id or not status_url:
        raise _NetworkError("上传响应缺少 task_id / status_url")
    return task_id, status_url


def _download_srt(
    ctx: ToolContext, srt_url: str, audio_path: str
) -> str:
    """下载 SRT 并原子写入音频同目录，返回保存路径。"""
    last_error = ""
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        ctx.check_cancel()
        try:
            _status, body = _request("GET", srt_url, timeout=REQUEST_TIMEOUT)
        except _HttpError as exc:
            if exc.status == 409:
                last_error = "服务端任务尚未完成"
            elif exc.status in (404, 422):
                raise ToolError(
                    f"下载字幕失败（HTTP {exc.status}）: {exc.detail}"
                ) from exc
            else:
                last_error = f"HTTP {exc.status}: {exc.detail}"
        except _NetworkError as exc:
            last_error = str(exc)
        else:
            if not body.strip():
                last_error = "下载的字幕内容为空"
            else:
                target = os.path.splitext(audio_path)[0] + ".srt"
                temp = target + ".part"
                try:
                    with open(temp, "wb") as handle:
                        handle.write(body)
                    os.replace(temp, target)
                except OSError as exc:
                    try:
                        if os.path.exists(temp):
                            os.remove(temp)
                    except OSError:
                        pass
                    raise ToolError(f"保存字幕失败: {exc}") from exc
                return target

        if attempt < DOWNLOAD_RETRIES:
            _sleep(ctx, 2.0)

    raise ToolError(f"下载字幕失败: {last_error}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def transcribe_audio_batch(
    base_url: str,
    files: List[str],
    language: str = "Chinese",
    ctx: Optional[ToolContext] = None,
    poll_interval: float = POLL_INTERVAL,
) -> TaskResult:
    """批量上传音频并自动等待、下载字幕。

    参数:
        base_url:      服务地址，如 https://xxxx.gradio.live
        files:         音频文件/文件夹路径列表（文件夹递归扫描音频）
        language:      语种全名（Chinese/English/...）
        poll_interval: 轮询间隔（秒），主要供测试调小
    """
    ctx = ctx or ToolContext()

    try:
        base = normalize_base_url(base_url)
    except ToolError as exc:
        return TaskResult(success=False, message=str(exc))

    audio_files, pre_errors = _collect_audio_files(files)
    if not audio_files:
        message = "没有可上传的音频文件"
        if pre_errors:
            message += "：" + "；".join(pre_errors[:3])
        return TaskResult(
            success=False, message=message, warnings=list(pre_errors)
        )

    language = (str(language or "").strip()) or "Chinese"
    ctx.log(f"服务地址: {base}")
    ctx.log(
        f"共 {len(audio_files)} 个音频，语种: {language}，"
        f"服务端并发上限: {MAX_ACTIVE_TASKS}"
    )
    for error in pre_errors:
        ctx.warn(error)

    try:
        _check_health(ctx, base)
    except ToolError as exc:
        return TaskResult(
            success=False, message=str(exc), warnings=list(pre_errors)
        )

    total = len(audio_files)
    pending = list(audio_files)
    active: List[_TaskState] = []
    outputs: List[str] = []
    links: List[str] = []
    failures: List[str] = list(pre_errors)
    done = 0
    failed = 0
    pre_failed = len(pre_errors)
    cancelled = False
    abort_error = ""
    queue_full_log_at = 0.0
    network_errors = 0

    try:
        while pending or active:
            ctx.check_cancel()

            # ---- 上传：串行填充，活动任务不超过服务端上限 ----
            while pending and len(active) < MAX_ACTIVE_TASKS:
                ctx.check_cancel()
                path = pending[0]
                name = os.path.basename(path)
                ctx.progress(
                    percent=_percent(done + failed, total),
                    file=name,
                    detail=(
                        f"上传中 · 活动任务 {len(active)}/{MAX_ACTIVE_TASKS}"
                    ),
                )

                upload_path = path
                temp_dir = ""
                if _needs_transcode(path):
                    index = total - len(pending) + 1
                    try:
                        upload_path, temp_dir = _prepare_for_upload(
                            ctx, path, index, total
                        )
                    except CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - 单个文件转换失败不中断批次
                        pending.pop(0)
                        failed += 1
                        detail = f"音频转换失败: {exc}"
                        failures.append(f"{name}: {detail}")
                        ctx.error(f"{name}: {detail}")
                        continue

                try:
                    task_id, status_url = _create_remote_task(
                        base, upload_path, language
                    )
                except _HttpError as exc:
                    if exc.status == 429:
                        now = time.monotonic()
                        if now - queue_full_log_at > 30.0:
                            queue_full_log_at = now
                            ctx.warn(
                                "服务端队列已满（最多 5 个任务），"
                                "等待空位后自动重试…"
                            )
                        break
                    if exc.status == 400:
                        pending.pop(0)
                        failed += 1
                        failures.append(f"{name}: {exc.detail}")
                        ctx.error(f"上传被拒绝: {name} — {exc.detail}")
                        continue
                    if exc.status == 503:
                        raise ToolError(exc.detail) from exc
                    pending.pop(0)
                    failed += 1
                    detail = f"HTTP {exc.status}: {exc.detail}"
                    failures.append(f"{name}: {detail}")
                    ctx.error(f"上传失败: {name} — {detail}")
                    continue
                except _NetworkError as exc:
                    network_errors += 1
                    if network_errors >= 3:
                        raise ToolError(
                            f"连续 {network_errors} 次无法连接服务 {base}: {exc}"
                        ) from exc
                    ctx.warn(
                        f"上传失败，稍后重试（{network_errors}/3）: {exc}"
                    )
                    break
                finally:
                    if temp_dir:
                        shutil.rmtree(temp_dir, ignore_errors=True)

                network_errors = 0
                pending.pop(0)
                task = _TaskState(
                    audio_path=path,
                    filename=name,
                    task_id=task_id,
                    status_url=status_url,
                )
                active.append(task)
                link_line = f"{name}  ->  {status_url}"
                links.append(link_line)
                # 实时推给面板：上传受理成功即显示，不等整批任务结束
                ctx.link(link_line)
                ctx.success(
                    f"已受理: {name}（活动任务 "
                    f"{len(active)}/{MAX_ACTIVE_TASKS}）"
                )
                ctx.log(f"  查询地址: {status_url}")

            # ---- 轮询：并行查询所有活动任务 ----
            for task in list(active):
                ctx.check_cancel()

                if time.monotonic() - task.created_at > TASK_TIMEOUT:
                    active.remove(task)
                    failed += 1
                    failures.append(
                        f"{task.filename}: 等待超时（超过 "
                        f"{TASK_TIMEOUT / 60:.0f} 分钟）"
                    )
                    ctx.error(f"{task.filename}: 等待超时，已放弃")
                    continue

                try:
                    _status, body = _request(
                        "GET", task.status_url, timeout=REQUEST_TIMEOUT
                    )
                    data = json.loads(body.decode("utf-8", "replace"))
                except _HttpError as exc:
                    if exc.status == 404:
                        active.remove(task)
                        failed += 1
                        failures.append(
                            f"{task.filename}: 任务不存在或已过期被清理"
                        )
                        ctx.error(
                            f"{task.filename}: 任务不存在或已过期被清理"
                        )
                    else:
                        task.network_errors += 1
                        if task.network_errors >= MAX_POLL_ERRORS:
                            active.remove(task)
                            failed += 1
                            failures.append(
                                f"{task.filename}: 查询失败（HTTP "
                                f"{exc.status}）: {exc.detail}"
                            )
                            ctx.error(
                                f"{task.filename}: 连续查询失败，已放弃"
                            )
                        else:
                            task.warn_error(
                                ctx,
                                f"{task.filename}: 查询状态失败（HTTP "
                                f"{exc.status}），稍后重试",
                            )
                    continue
                except (_NetworkError, ValueError) as exc:
                    task.network_errors += 1
                    if task.network_errors >= MAX_POLL_ERRORS:
                        active.remove(task)
                        failed += 1
                        failures.append(f"{task.filename}: 查询失败: {exc}")
                        ctx.error(f"{task.filename}: 连续查询失败，已放弃")
                    else:
                        task.warn_error(
                            ctx,
                            f"{task.filename}: 查询状态失败，稍后重试（{exc}）",
                        )
                    continue

                task.network_errors = 0
                status = str(data.get("status") or "")
                progress_text = str(data.get("progress") or "").strip()

                if status in ("queued", "processing"):
                    task.status = status
                    if progress_text:
                        task.progress = progress_text
                    if task.should_log(status, progress_text):
                        label = "排队中" if status == "queued" else "识别中"
                        detail = f" — {progress_text}" if progress_text else ""
                        ctx.log(f"{task.filename}: {label}{detail}")
                    continue

                if status == "succeeded":
                    srt_url = _absolute_url(
                        base, str(data.get("srt_url") or "")
                    )
                    if not srt_url:
                        srt_url = task.status_url.rstrip("/") + "/srt"
                    ctx.log(f"{task.filename}: 转换完成，开始下载字幕…")
                    try:
                        srt_path = _download_srt(ctx, srt_url, task.audio_path)
                    except ToolError as exc:
                        active.remove(task)
                        failed += 1
                        failures.append(f"{task.filename}: {exc}")
                        ctx.error(f"{task.filename}: {exc}")
                        continue

                    active.remove(task)
                    done += 1
                    outputs.append(srt_path)
                    extra: List[str] = []
                    segment_count = data.get("segment_count")
                    if segment_count is not None:
                        extra.append(f"{segment_count} 条")
                    duration = data.get("duration")
                    if isinstance(duration, (int, float)):
                        extra.append(f"时长 {float(duration):.1f}s")
                    suffix = f"（{'，'.join(extra)}）" if extra else ""
                    ctx.success(
                        f"{task.filename}: 字幕已保存 → {srt_path}{suffix}"
                    )
                    continue

                if status == "failed":
                    active.remove(task)
                    failed += 1
                    error = str(
                        data.get("error")
                        or progress_text
                        or "服务端处理失败"
                    )
                    failures.append(f"{task.filename}: {error}")
                    ctx.error(f"{task.filename}: 转换失败 — {error}")
                    continue

                # 未知状态：保留在活动列表继续观察，日志限频
                task.status = status or task.status
                if task.should_log(task.status, progress_text):
                    ctx.warn(
                        f"{task.filename}: 未知状态 {status or '(空)'}，继续等待"
                    )

            current_file = active[0].filename if active else ""
            ctx.progress(
                percent=_percent(done + failed, total),
                file=current_file,
                detail=(
                    f"已完成 {done}/{total}，失败 {failed}，"
                    f"活动任务 {len(active)}"
                ),
            )

            if pending or active:
                # 队列满且没有活动任务时退避久一点，避免空转
                delay = poll_interval
                if pending and not active:
                    delay = max(delay, QUEUE_FULL_BACKOFF)
                _sleep(ctx, delay)

    except CancelledError:
        cancelled = True
    except ToolError as exc:
        abort_error = str(exc)
        ctx.error(abort_error)

    unfinished = len(active)
    total_failed = failed + pre_failed
    pre_note = (
        f"（另有 {pre_failed} 个无效路径/空文件）" if pre_failed else ""
    )

    if cancelled:
        message = (
            f"已取消：成功 {done} 个，失败 {failed} 个{pre_note}，"
            f"未完成 {unfinished} 个"
        )
        if unfinished:
            message += "（已上传的任务可能仍在服务端处理，查询地址见面板）"
    elif abort_error:
        message = (
            f"已中止：{abort_error}（成功 {done} 个，失败 {failed} 个"
            f"{pre_note}，未完成 {unfinished} 个）"
        )
    else:
        message = (
            f"完成：成功 {done} 个，失败 {failed} 个{pre_note}，"
            f"共 {total} 个音频"
        )

    if not cancelled and not abort_error:
        ctx.progress(percent=100.0, detail="全部任务已结束")

    return TaskResult(
        success=bool(
            not cancelled and not abort_error and total_failed == 0 and done > 0
        ),
        message=message,
        outputs=outputs,
        warnings=failures,
        links=links,
    )


__all__ = [
    "LANGUAGE_CHOICES",
    "MAX_ACTIVE_TASKS",
    "normalize_base_url",
    "transcribe_audio_batch",
]
