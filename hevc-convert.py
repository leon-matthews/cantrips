#!/usr/bin/env python3

"""
HEVC Convert

Recompress video files in place to HEVC using FFMPEG and libx265.

TODO:
    - Change FFMPEG arguments based on command line options.
    - Fill implementation of secure_copy()


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
import logging
from pathlib import Path
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

    Returns:
        None
    """
    logger.info("START compressing HEVC MP4 video: %s", original.name)

    # Recompress output original into temporary folder
    output_name = original.with_suffix('.mp4').name
    output_video = temp_folder / output_name
    args = build_ffmpeg_args(original, output_video, options)

    print()
    print("="*80)
    print(original.name)
    print("="*80)
    print(" ".join(args))
    print()

    logger.info("Execute FFMPEG, output to: %s", output_video)
    if not options.dry_run:
        subprocess.run(args, check=True)

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
                logger.info("Skipping folder: %s", video)
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
        help='only show which files would be transfered',
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
    logging.basicConfig(
        format='%(message)s',
        level=logging.INFO,
    )
    status = main(options)
    sys.exit(status)
