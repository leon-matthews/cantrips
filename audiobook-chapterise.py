#!/usr/bin/env python3

"""
Break a single-file audio book into separate MP3 files.

I like to use listen to audiobooks while I work on DIY projects. I use cheap
little MP3 players because phones are too expensive to replace when they get
paint on them or get water-blasted.

The original file is left untouched. Output files are written into a new
directory in the same folder as the original file.

Requires ffmpeg binaries, the Python package 'rich', and chapter markers
in the input file.

TODO:
    * Replace `colorama` for coloured output with `rich`.
    * Refactor with on eye on responsibilities.

"""

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, TypeAlias

from rich.console import Console


console = Console()
JsonList: TypeAlias = list[dict[str, Any]]


class Chapteriser:
    """
    Break large single-file audio books into small MP3 files.
    """
    def __init__(self, path, chapters, add_index=0, verbose=False):
        self.path = path
        self.add_index = add_index
        self.extension = '.mp3'
        self.chapters = chapters
        self.folder = None
        self.num_chapters = len(chapters)
        self.padding = max(2, math.ceil(math.log(self.num_chapters + add_index + 1, 10)))
        self.verbose = verbose

    def extract(self):
        # Output folder
        self.folder = self.path.parent / self.make_foldername()
        if self.folder.exists():
            console.print(f"Output folder already exists: {self.folder}")
            raise SystemExit(1)
        self.folder.mkdir()

        # Output files
        with change_folder(self.folder, verbose=self.verbose):
            for number, chapter in enumerate(self.chapters, 1):
                filename = self.make_filename(chapter)
                console.print(f"[{number}/{self.num_chapters}] {filename}")
                self._extract_chapter(filename, chapter)

    def _extract_chapter(self, output_path, chapter):
        """
        Extract a single chapter.
        """
        start = float(chapter['start_time'])
        end = float(chapter['end_time'])
        input_path = f"../{self.path.name}"
        ffmpeg_extract_audio(input_path, start, end, output_path, verbose=self.verbose)

    def preview(self):
        """
        Print multi-column string show preview of output folder.
        """
        last = self.chapters[-1]
        num_seconds = float(last['end_time'])
        total = self.human_time(num_seconds)
        console.print(f"Found {self.num_chapters:,} chapters within {total} of audio. ")
        average = self.human_time(num_seconds / self.num_chapters)
        console.print(f"That is an average of {average} per chapter.")
        folder = self.make_foldername()
        console.print(f"Create folder: '{folder}'")
        lines = []
        for number, chapter in enumerate(self.chapters):
            lines.append(self.make_filename(chapter))
        print(columnise(lines))

    def make_filename(self, chapter):
        """
        Build the output file name for given (zero-based) index.
        """
        index = chapter['id'] + self.add_index
        # Replace empty or numeric only titles
        title = chapter.get('tags', {}).get('title', '')
        stripped = title.strip('1234567890')
        if not stripped:
            title = f"Chapter {index+1}"
        prefix = f"{index+1:0>{self.padding}}"
        filename = f"{prefix}. {title}{self.extension}"
        filename = clean(filename)
        return filename

    def make_foldername(self):
        folder = clean(self.path.stem)
        return folder

    def human_time(self, seconds: int) -> str:
        """
        Human friendly formatted duration.

        eg. '1 hour and 15 minutes'
        """
        hours, minutes, _ = self._time(seconds)
        hours_part = f"{hours} hour" if hours == 1 else f"{hours} hours"
        minutes_part = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
        if hours > 0:
            return f"{hours_part} and {minutes_part}"
        else:
            return f"{minutes_part}"

    def short_time(self, seconds: int, truncate=False) -> str:
        """
        eg. '00:45'
        """
        hours, minutes, seconds = self._time(seconds)
        time = f"{hours:0>2}:{minutes:0>2}:{int(seconds):0>2}"
        if not truncate:
            time += str(seconds - int(seconds))[1:]
        return time

    def _time(self, seconds: int):
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return int(hours), int(minutes), float(seconds)


@contextmanager
def change_folder(folder: Path, verbose: bool = False) -> None:
    """
    Context manager to change working dir, then restore it again.

    Args:
        folder:
            Path to folder to change to.
        verbose:
            Print directory comman

    Returns:
        None
    """
    old = Path.cwd()
    if verbose:
        console.print(f"cd {folder}")
    os.chdir(folder)
    yield
    os.chdir(old)


def columnise(strings: list[str]) -> str:
    """
    Format small strings into columns.

    Args:
        strings:
            List of small strings.

    Returns:
        Multiline string.
    """
    width, _ = shutil.get_terminal_size()
    if not strings:
        return ''
    longest = len(max(strings, key=len))
    max_columns = int(width / (longest + 1))
    max_columns = max(1, max_columns)
    num_rows = int(len(strings) / max_columns) + 1
    padded = ["{0:<{1}}".format(s, longest) for s in strings]
    lines = []
    step = num_rows
    for i in range(num_rows):
        parts = padded[i::step]
        line = " ".join(parts)
        lines.append(line)
    return '\n'.join(lines)


def clean(filename: str) -> str:
    """
    Perform best-effort to clean given string into a legal filename.

    Preserves case.

    Args:
        filename:
            Proposed file name.

    Returns:
        Cleaned version of input file.
    """
    string = filename.strip()
    # Colon to hyphen
    string = string.replace(':', ' - ')
    # Replace illegal characters
    string = re.sub(r'[^\w\' .,\(\)]', ' ', string)
    # Compact runs of whitespace
    string = re.sub(r'\s+', ' ', string)
    return string


def ffmpeg_extract_audio(
    path: Path,
    start: float,
    end: float,
    output: Path,
    *,
    quality: int = 6,
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run ``ffmpeg`` to extract audio clip from input.

    Args:
        path:
            Path to media file.
        start:
            Number of seconds from start of file to start extractions from.
        end:
            Seconds from start of file to stop extraction.
        output:
            Path to output file to write clip to.
        quality:
            Optionally overide MP3 LAME quality setting. The default value is
            chosen to give small file sizes with acceptable quality for audio
            books.
        verbose:
            Print command-line before running it.

    Returns:
        Subprocess completed process.
    """
    args = [
        'ffmpeg',
        '-i', str(path),
        '-hide_banner',
        '-vn', '-sn', '-dn',    # Drop video, subtitle and data streams
        '-ss', str(start),
        '-to', str(end),
        '-codec:a',
        'libmp3lame',
        '-ac', '2',
        '-qscale:a', str(quality),
        '-n',                   # Don't overwrite existing
        str(output),
    ]
    result = run(args, verbose=verbose)
    return result


def ffprobe_show_chapters(path: Path, verbose: bool = False) -> JsonList:
    """
    Run ``ffprobe`` and collect its output.

    Each chapter, if found, has data something like this:

        [...,
        {
            'end': 31725609,
            'end_time': '31725.609000',
            'id': 19,
            'start': 29872378,
            'start_time': '29872.378000',
            'tags': {'title': '020'},
            'time_base': '1/1000'
        },
        ...]

    Args:
        path:
            Path to media file.
        verbose:
            Print subprocess command before running it.

    Returns:
        List of chapter data.
    """
    args = [
        'ffprobe',
        '-i', str(path),
        '-hide_banner',
        '-print_format',
        'json',
        '-show_chapters',
    ]
    result = run(args, verbose=verbose)
    data = json.loads(result.stdout)
    chapters = data.get('chapters', [])
    if not chapters:
        print('No chapters found in audio file.', file=sys.stderr)
        raise SystemExit(3)
    return chapters


def run(args: list[str], verbose: bool = False) -> subprocess.CompletedProcess:
    """
    Run external command and capture its output.

    Thin wrapper around `subprocess.run()`.

    Args:
        args:
            Command and its arguments.
        verbose:
            Print command before executing it.

    Raises:
        SystemExit:
            If command not found, or error running command.

    Returns:
        Subprocess completed process.
    """
    if verbose:
        console.print(' '.join([shlex.quote(arg) for arg in args]))
    try:
        result = subprocess.run(args, capture_output=True, check=True)
    except FileNotFoundError:
        command = args[0]
        console.print(f"Command '{command}' not found. Please install.")
        raise SystemExit(2)
    except subprocess.CalledProcessError as e:
        error = e.stderr.decode().strip()
        message = f"Command error {e.returncode}: {error!r}"
        console.print(message)
        raise SystemExit(1)
    return result


def parse(arguments: list[str]) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Args:
        arguments:
            Argument strings from `sys.argv`.

    Returns:
        Collected options.
    """
    description = "Break audio book into multiple files, one per chapter."
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        '-a', '--add', action='store', metavar='NUM',
        help='number to add to track index')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help="print commands as they are run")
    parser.add_argument(
        '-y', '--yes', action='store_false', dest='confirm',
        help="assume yes; do not ask for confirmation")
    parser.add_argument('path', metavar='PATH', help='audio file to process')
    options = parser.parse_args()
    return options


def main(options: argparse.Namespace) -> int:
    """
    Command's entry point.

    Args:
        options:
            Command-line options parsed by `parse()`.

    Returns:
        Integer error code.
    """
    # Examine file
    path = Path(options.path)
    with change_folder(path.parent, verbose=options.verbose):
        chapters = ffprobe_show_chapters(path.name, verbose=options.verbose)

    # Check with user
    add_index = 0
    if options.add:
        add_index = int(options.add)
    chapteriser = Chapteriser(path, chapters, add_index=add_index, verbose=options.verbose)
    chapteriser.preview()

    if options.confirm:
        console.print("Do you wish to proceed [y/N]?")
        response = input().lower()
        if not response.startswith('y'):
            raise SystemExit(0)
    chapteriser.extract()
    return 0


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    sys.exit(main(options))
