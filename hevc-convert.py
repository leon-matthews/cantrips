#!/usr/bin/env python3

"""
HEVC Convert

Recompress video files in place to HEVC using FFMPEG and libx265.

TODO:
    - Change FFMPEG arguments based on command line options.
    - Fill implementation of secure_copy()
"""

import argparse
from pathlib import Path
from pprint import pprint as pp
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


def hevc_convert(video: Path, temp_folder: Path) -> None:
    """
    Convert video in-place.
    """
    # Recompress into new file
    output_video = temp_folder / video.name
    args = (
        'ffmpeg',
        '-hide_banner',
        '-i', str(video),
        '-c:v', 'libx265',
        '-x265-params', 'log-level=warning',
        '-preset', 'slow',
        '-crf', '28',       # 28 default, 26 for better quality.
        '-c:a', 'copy',
        '-f', 'mp4',
        str(output_video),
    )

    # Scale
    # -2 means force divisible by 2
    # -vf scale=w=-2:h=720:force_original_aspect_ratio=decrease
    # -vf scale=w=-2:h=1080:force_original_aspect_ratio=decrease

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
            hevc_convert(video, Path(temp_folder))

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

    # Resize
    resize_parser = parser.add_mutually_exclusive_group()
    resize_parser.add_argument(
        '--720',
        action='store_true',
        help="downsize to 720p, keeping aspect ratio",
    )

    resize_parser.add_argument(
        '--1080',
        action='store_true',
        help="downsize to 1080p, keeping aspect ratio",
    )

    options = parser.parse_args(args)
    pp(options)
    return options


if __name__ == '__main__':
    options = parse_arguments(sys.argv[1:])
    status = main(options)
    sys.exit(status)
