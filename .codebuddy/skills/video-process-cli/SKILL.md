---
name: video-process-cli
description: This skill should be used when driving the video-process project (d:/work/python/video-process) from a terminal instead of its PyQt6 GUI — merging, trimming, cutting out ad segments, extracting clips/audio/frames, compressing, converting, and working with SRT/ASS/剪映/必剪 subtitles, timeline recalculation, and 繁简 conversion. Trigger it for requests like "批量合并这些视频", "把这段广告剪掉", "把字幕转成 ASS", "重算时间线", "简体转台湾正体", or any scripted/batch media processing in this repository.
---

# 视频处理工具集 · 命令行版

## Overview

The repository ships one CLI that exposes every GUI tool as a subcommand. Parameters, defaults, ranges and validation come from the same declarative spec in `video_process/defs/` that the GUI renders, so CLI and GUI never drift. Never hand-write ffmpeg commands for these tasks — call the CLI.

Entry points (pick whichever exists in the environment):

```bash
python -m video_process.cli <subcommand> [options]   # source checkout
video-process-cli <subcommand> [options]             # after `pip install -e .`
dist/video-process/视频处理工具集-命令行.exe ...        # packaged console build
```

`--help` on any subcommand is authoritative for parameter names, defaults and allowed values.

## Subcommands

| Subcommand | Purpose | Required options |
|---|---|---|
| `merge` | Merge all videos in a folder, sorted by filename | `--directory` |
| `trim-edges` | Keep an absolute `[start, end)` range | `--input-path` |
| `batch-trim` | Cut a fixed duration off head/tail of every video in a folder | `--directory` |
| `remove-segments` | Delete time ranges and join the rest (ad removal) | `--input-path --segments` |
| `extract-segments` | Export each range as its own file | `--input-path --segments` |
| `split` | Split at time points | `--input-file --split-points` |
| `compress` | AMF hardware compression | `--input-path` |
| `convert` | Convert to plain MP4 (fast remux / CPU / AMD) | `--input-path` |
| `audio` | Extract every audio track as MP3 | `--input-path` |
| `frame` | Save one frame as JPG | `--input-path` |
| `srt2ass` | SRT → ASS with Chinese-friendly styling | `--input-path` |
| `srt-remove` | Delete ranges from an SRT and shift later cues | `--input-file --ranges-spec` |
| `jianying-subtitle` | Export 剪映 draft subtitles to SRT/TXT | `--input-path` |
| `bcut-subtitle` | Export 必剪 `.bjson` subtitles to SRT/TXT | `--input-path` |
| `timeline` | Recalculate a timeline with `[广告]` segments removed | `--text` (can read stdin) |
| `zh-convert` | 简体/繁体 conversion via OpenCC | `--text` (can read stdin) |

Option names are the spec field names in kebab-case (`output_dir` → `--output-dir`). Booleans accept both `--flag` and `--no-flag` (e.g. `--no-recursive`, `--no-sync-srt`).

Common options per subcommand:

| Option | Effect |
|---|---|
| `-q`, `--quiet` | Show only warnings and errors |
| `--overwrite` | Allow overwriting existing outputs for this run (does not persist) |
| `--version` | Print version (place before the subcommand) |

Frequently used value options: `--mode` (`merge`/`convert` take `1|2|3`, others take `fast|amd`, `split` also `precise`), `--output-dir`, `--segments`, `--quality`, `--cqp/--qp`, `--export-format srt|txt|both`.

## Contract

- Logs, progress and the result summary go to **stderr**; output paths or plain text go to **stdout**, one item per line — redirect or pipe freely.
- Exit codes: `0` success, `1` execution failure, `2` bad arguments.
- `Ctrl+C` cancels cleanly (ffmpeg process tree is killed).
- Defaults come from `~/.video-process/config.json` (shared with the GUI). There is no `settings` subcommand; edit that file or use the GUI to change defaults such as `overwrite`, `amf_quality`, `recursive_scan`.
- Time format: `90` (seconds), `1:30`, `1:30:45`, `1:30:45.500`, plus the keywords `开头` (0) and `结尾` (end of file). Ranges are `start-end`, multiple ranges comma-separated: `开头-1:00,5:00-结尾`.

## Typical invocations

```bash
# Merge a season folder, sorted by filename
python -m video_process.cli merge "D:/剧集/S01"

# Remove two ad ranges, frame-accurate AMD encode, overwrite the previous result
python -m video_process.cli remove-segments "D:/剧集/01.mp4" \
  --segments "开头-1:00,5:00-6:00" --mode amd --overwrite

# Cut 15s head + 1:30 tail from every video, output into cut/
python -m video_process.cli batch-trim "D:/录播" --start-cut 15 --end-cut 1:30

# Screenshot at 1:30, and extract all audio tracks quietly
python -m video_process.cli frame "D:/a.mp4" --time-position 1:30
python -m video_process.cli audio "D:/a.mp4" -q

# Timeline recalculation through a pipe (result on stdout, ad CSV on stderr)
cat timeline.txt | python -m video_process.cli timeline --text - > new.txt

# 简体 → 台湾正体（含习惯用语）
python -m video_process.cli zh-convert --text "软件和鼠标" --conversion s2twp
```

## Working on the CLI itself

- Adding a GUI tool automatically adds a subcommand: register the `ToolDefinition` in `video_process/defs/` (`video.py`, `media.py`, `subtitle.py`, `misc.py`) with `specs` + `runner`. Do not touch `cli.py` for new tools.
- `video_process/cli.py` must never import PyQt6 — the packaged console build would drag in Qt.
- Subcommand names are the tool ids; tool ids with `runner=None` (`settings`) are skipped.
- Packaging: `python build.py` (both entries), `python build.py --onefile --targets cli` (console-only single file), `--clean`, `--no-ffmpeg`, `--ffmpeg-dir DIR`.
