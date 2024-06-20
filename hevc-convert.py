#!/usr/bin/env python3

"""
HEVC Convert

Recompress video files in place to HEVC using FFMPEG and libx265.

TODO:
    - Change FFMPEG arguments based on command line options.
    - Fill implementation of secure_copy()
"""

import argparse
import logging
from pathlib import Path
from pprint import pprint as pp
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


logger = logging.getLogger(__name__)


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
        self.global_options = ['-hide_banner', '-nostdin']

    def args(self) -> list[str]:
        args = ['ffmpeg'] + self.global_options
        args += self.input_options
        args += ['-i', str(self.input_path)]
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
    builder.output_options += ['-preset', 'slow']
    if options.better:
        builder.output_options += ['-crf', '26']
    else:
        builder.output_options += ['-crf', '28']

    # Audio
    if options.stereo:
        builder.output_options += [
            '-ac', '2',
            '-c:a', 'aac',
            '-b:a', '128k',
        ]
    else:
        builder.output_options += ['-c:a', 'copy']

    # Scale
    if options.scale_720:
        builder.output_options += [
            '-vf', 'scale=w=-2:h=720:force_original_aspect_ratio=decrease',
        ]
    if options.scale_1080:
        builder.output_options += [
            '-vf', 'scale=w=-2:h=1080:force_original_aspect_ratio=decrease',
        ]

    # Tune
    if options.animation:
        builder.output_options += ['-tune', 'animation']

    return builder.args()


def hevc_convert(video: Path, temp_folder: Path, options: argparse.Namespace) -> None:
    """
    Convert video in-place.

    Args:
        video:
            Path to input file.
        temp_folder:
            Folder to save partially encoded file into.
        options:
            Command-line options

    Returns:
        None
    """
    # Recompress into new file
    output_video = temp_folder / video.name
    builder = FFmpegArgumentBuilder(video, output_video)
    args = build_ffmpeg_args(video, output_video, options)

    print()
    print("="*80)
    print(video.name)
    print("="*80)
    print(" ".join(args))
    print()
    subprocess.run(args, check=True)

    # Replace original file
    shutil.copyfile(output_video, video)

    # Remove new file
    output_video.unlink()


def secure_copy(old: Path, new: Path, exist_ok: bool = False) -> None:
    """
    Copy file into new location avoiding partial copy errors.

    Even if interupted, file should not be left in a partially copied state.
    It is first copied to the destination folder using a temporary name,
    then renamed to the final name only when copy is completed.

    Args:
        old:
            Current
        new:
            Location to copy file to.
        exist_ok:
            Will silently overwrite any existing file if true.

    Returns:
        None
    """


def main(options: argparse.Namespace) -> int:
    videos = [Path(name) for name in options.videos]

    with TemporaryDirectory(prefix='hevc-convert-') as temp_folder:
        for video in videos:
            hevc_convert(video, Path(temp_folder), options)

    return 0


def parse_arguments(args: list[str]) -> argparse.Namespace:
    """
    Create and run `argparse`-based command parser.
    """
    parser = argparse.ArgumentParser(
        description="Recompress video files in place",
    )

    # Files
    parser.add_argument(
        dest='videos',
        metavar='VIDEO',
        nargs='+',
        help="One or more video files to recompress using x265",
    )

    # Quality
    parser.add_argument(
        '-b',
        '--better',
        action='store_true',
        help='improve video quality by changing x265 CRF value from 28 to 26',
    )

    # Audio
    parser.add_argument(
        '--stereo',
        action='store_true',
        help='Force stereo audio, downmixing channels if necessary',
    )

    # Resize
    resize_parser = parser.add_mutually_exclusive_group()
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

    # Animation
    parser.add_argument(
        '--animation',
        action='store_true',
        help='Hint to encoder that input is animation',
    )

    options = parser.parse_args(args)
    return options


if __name__ == '__main__':
    options = parse_arguments(sys.argv[1:])
    status = main(options)
    sys.exit(status)
