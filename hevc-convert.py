#!/usr/bin/env python3

"""
HEVC Convert

Recompress video files in place to HEVC using FFMPEG and libx265.
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
        '-crf', '28',
        '-c:a', 'copy',
        '-f', 'mp4',
        str(output_video),
    )
    subprocess.run(args, check=True)

    # Replace original file
    shutil.copyfile(output_video, video)

    # Remove new file
    output_video.unlink()


def main(options: argparse.Namespace) -> int:
    videos = [Path(name) for name in options.videos]

    with TemporaryDirectory(prefix='hevc-convert-') as temp_folder:
        temp_folder = Path(temp_folder)
        for video in videos:
            hevc_convert(video, temp_folder)

    return 0


def parse_arguments(args):
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
    )

    options = parser.parse_args(args)
    return options


if __name__ == '__main__':
    options = parse_arguments(sys.argv[1:])
    status = main(options)
    sys.exit(status)
