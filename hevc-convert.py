#!/usr/bin/env python3

"""
HEVC Convert

Recompress video files in place to HEVC using FFMPEG and libx265.

2026-06-15
    TODO It might be time to try AV1 again. As a starting point:
    ffmpeg -i input.mp4 -c:v libsvtav1 -preset 6 -crf 28 \
        -svtav1-params tune=0:film-grain=8 -c:a copy output.mkv

2025-08-27
    Experimented with AV1 encoding, using the `libsvtav1` encoder. Underwhelming,
    but more experimentation/comparison needed:

        ffmpeg -i input.mp4 \
            -ss 00:01:00 -to 00:02:00 \
            -map 0:v:0 -map 0:a:0 -map 0:s? \
            -c:v libsvtav1 -crf 35 -preset 4 \
            -c:a libopus -c:s copy output.mkv

"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading

from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


class FFmpegArgumentBuilder:
    """
    Build list of FFmpeg command-line arguments.

        $ ffmpeg [global_options] \
            {[input_file_options] -i input_url} \
            {[output_file_options] output_url}

    """
    global_options: list[str]
    input_options: list[str]
    output_options: list[str]

    def __init__(self, input_path: Path, output_path: Path):
        self.input_options = []
        self.input_path = input_path
        self.output_options = []
        self.output_path = output_path
        self.global_options = [
            '-hide_banner',
            '-nostdin',
            '-loglevel', 'error',
            '-nostats',
            '-progress', 'pipe:1',
        ]

    def args(self) -> list[str]:
        args = ['ffmpeg'] + self.global_options
        args += self.input_options
        args += ['-i', str(self.input_path)]
        args += ['-map', '0:v:0']       # Keep first video stream
        args += ['-map', '0:a:0']       # Keep first audio stream
        args += ['-map', '0:s?']        # Keep all subtitle streams
        args += self.output_options
        args += [str(self.output_path)]
        return args


def build_ffmpeg_args(
    input_path: Path,
    output_path: Path,
    options: argparse.Namespace,
) -> list[str]:
    """
    Prepare list of command-line arguments ready for `subprocess.run()`

    Opinionated choice of arguments to get decent x265/HEVC videos.

    Args:
        input_path:
            Path to input file.
        output_path:
            Folder to save partially encoded file into.
        options:
            Command-line options

    Returns:
        List of arguments.
    """
    # x265/HEVC
    builder = FFmpegArgumentBuilder(input_path, output_path)
    builder.output_options += [
        '-c:v', 'libx265',
        '-x265-params', 'log-level=warning',
    ]

    # Quality
    if options.better:
        builder.output_options += ['-preset', 'slower', '-crf', '26']
    else:
        builder.output_options += ['-preset', 'slow', '-crf', '28']

    # Video filters
    filters = []
    if options.deinterlace:
        filters.append('bwdif=mode=send_field:parity=auto:deint=all')
    if options.scale_480:
        filters.append("scale=w=-2:h='min(480,ih)'")
    elif options.scale_720:
        filters.append("scale=w=-2:h='min(720,ih)'")
    elif options.scale_1080:
        filters.append("scale=w=-2:h='min(1080,ih)'")
    if filters:
        builder.output_options += ['-vf', ','.join(filters)]

    # Audio
    if options.stereo:
        builder.output_options += [
            '-ac', '2',
            '-c:a', 'aac',
            '-b:a', '128k',
        ]
    else:
        builder.output_options += ['-c:a', 'copy']

    # Subtitles
    builder.output_options += [
        '-c:s', 'copy',
    ]

    # Tune
    if options.animation:
        builder.output_options += ['-tune', 'animation']

    return builder.args()


def ffprobe_duration(path: Path) -> float | None:
    """
    Return media duration in seconds, or None if unknown.
    """
    args = [
        'ffprobe',
        '-hide_banner',
        '-loglevel', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(path),
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


BAR_WIDTH = 40
# Approximate width consumed by every column except the description:
# percent (4) + separator (1) + elapsed (7) + separator (1) + remaining (7) + gaps (6).
OTHER_COLUMNS_WIDTH = BAR_WIDTH + 26


def _format_description(name: str, width: int) -> str:
    """Truncate with an ellipsis or right-pad ``name`` so it occupies ``width`` chars."""
    if len(name) > width:
        return name[: width - 1] + '…'
    return name.ljust(width)


def run_ffmpeg_with_progress(args: list[str], description: str, total: float | None) -> None:
    """
    Run ffmpeg, parsing its ``-progress`` stream to drive a rich progress bar.

    Raises:
        subprocess.CalledProcessError:
            On non-zero exit, with stderr attached.
    """
    desc_width = max(10, shutil.get_terminal_size().columns - OTHER_COLUMNS_WIDTH)
    description = _format_description(description, desc_width)

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=BAR_WIDTH),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout = proc.stdout
    stderr = proc.stderr
    assert stdout is not None and stderr is not None
    stderr_buf: list[str] = []
    stderr_thread = threading.Thread(target=lambda: stderr_buf.extend(stderr))
    stderr_thread.start()

    with Progress(*columns) as progress:
        task = progress.add_task(description, total=total)
        for line in stdout:
            if line.startswith('out_time_us='):
                value = line.split('=', 1)[1].strip()
                if value.isdigit():
                    progress.update(task, completed=int(value) / 1_000_000)
        if total is not None:
            progress.update(task, completed=total)

    proc.wait()
    stderr_thread.join()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=None, stderr=''.join(stderr_buf),
        )


def hevc_convert(original: Path, temp_folder: Path, options: argparse.Namespace) -> None:
    """
    Convert video in-place.

    Args:
        original:
            Path to input file.
        temp_folder:
            Folder to save partially encoded file into.
        options:
            Command-line options
    """
    suffix = '.mkv' if options.mkv else '.mp4'
    output_name = original.with_suffix(suffix).name
    output_video = temp_folder / output_name
    args = build_ffmpeg_args(original, output_video, options)

    if options.dry_run:
        print(' '.join(args))
        return

    duration = ffprobe_duration(original)
    try:
        run_ffmpeg_with_progress(args, original.name, duration)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stderr or '')
        raise

    dest = original.parent / output_name
    part = dest.with_name(dest.name + '.part')
    shutil.copyfile(output_video, part)
    part.replace(dest)
    output_video.unlink()


def main(options: argparse.Namespace) -> int:
    videos = [Path(name) for name in options.videos]

    with TemporaryDirectory(prefix='hevc-convert-') as temp_folder:
        for video in videos:
            if video.is_dir():
                print(f"Skipping folder: {video}", file=sys.stderr)
                continue

            hevc_convert(video, Path(temp_folder), options)

    return 0


def parse_arguments(args: list[str]) -> argparse.Namespace:
    """
    Create and run `argparse`-based command parser.
    """
    parser = argparse.ArgumentParser(
        description="Recompress video files in place",
    )

    # --animation
    parser.add_argument(
        '--animation', action='store_true',
        help='Hint to encoder that input is animation',
    )

    # --better, -b
    parser.add_argument(
        '-b', '--better', action='store_true',
        help='improve video quality by changing x265 CRF value from 28 to 26',
    )

    # --deinterlace
    parser.add_argument(
        '--deinterlace',
        action='store_true',
        help="Deinterlace using the 'bwdif' filter",
    )

    # --dry-run, -n
    parser.add_argument(
        '-n', '--dry-run', action='store_true',
        help='show the ffmpeg command for each input without executing it',
    )

    # --mkv, -m
    parser.add_argument(
        '-m', '--mkv', action='store_true',
        help="use '.mkv' as the output file extension instead of '.mp4'",
    )

    # --stereo, -s
    parser.add_argument(
        '-s', '--stereo', action='store_true',
        help='Force stereo audio, downmixing channels if necessary',
    )

    # --480, --720, --1080
    resize_parser = parser.add_mutually_exclusive_group()
    resize_parser.add_argument(
        '--480',
        action='store_true',
        dest='scale_480',
        help="downsize to 480p, keeping aspect ratio",
    )
    resize_parser.add_argument(
        '--720',
        action='store_true',
        dest='scale_720',
        help="downsize to 720p, keeping aspect ratio",
    )
    resize_parser.add_argument(
        '--1080',
        action='store_true',
        dest='scale_1080',
        help="downsize to 1080p, keeping aspect ratio",
    )

    # File arguments
    parser.add_argument(
        dest='videos',
        metavar='VIDEO',
        nargs='+',
        help="One or more video files to recompress using x265",
    )

    options = parser.parse_args(args)
    return options


if __name__ == '__main__':
    options = parse_arguments(sys.argv[1:])
    status = main(options)
    sys.exit(status)
