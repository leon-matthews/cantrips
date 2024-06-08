#!/usr/bin/env python3

"""
Break a single-file audio book into separate MP3 files.

I like to use listen to audiobooks while I work on DIY projects. Smart phones
are too expensive to replace when they get paint on them or get accidentally
power-washed, so I use cheap little MP3 players instead.

The original file is left untouched. Output files are written into a new
directory in the same folder as the original file. The goal is to be able to
produce a folder full of audio files, one-per-chapter. Something like::

    My Book/
        01. Chapter 1.mp3
        02. Chapter 2.mp3
        03. Chapter 3.mp3
        ...



Requirements:
    Requires the `ffmpeg` and `ffprobe` binaries, and the Python package 'rich'.
    https://ffmpeg.org/
    https://rich.readthedocs.io/en/latest/

TODO:
    * Finish replacing confirmation dialog.
    * Refactor with on eye on responsibilities.
    * Split by time chunks if chapters not available.

"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
import logging
from pathlib import Path
from pprint import pprint as pp
import re
import shlex
import subprocess
import sys
from typing import Any, TypeAlias, Union

from rich import print as rprint
from rich.columns import Columns
from rich.prompt import Confirm


Json: TypeAlias = Union[list, dict[str, Any]]
logger = logging.getLogger(__name__)


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
        rprint(f"cd {folder}")
    os.chdir(folder)
    yield
    os.chdir(old)


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
    # Colon to hyphen
    string = filename.strip()
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


def ffprobe(path: Path, verbose: bool = False) -> Json:
    """
    Run system's ``ffprobe`` binary against a media file and collect its output.

    Currently, we're capturing chapter and general format info, from a JSON
    packet that looks something like this::

        {
            'chapters': [
                ...,
                {
                    'end': 31725609,
                    'end_time': '31725.609000',
                    'id': 19,
                    'start': 29872378,
                    'start_time': '29872.378000',
                    'tags': {'title': '020'},
                    'time_base': '1/1000'
                },
                ...,
            ],
            'format': {
                'bit_rate': '63498',
                'duration': '29688.662494',
                ...,
            }
        }

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
        '-hide_banner',
        '-loglevel', 'warning',
        '-i', str(path),
        '-print_format',
        'json',
        '-show_chapters',
        '-show_format',
    ]

    data = {}
    try:
        result = run(args, verbose=verbose)
        data = json.loads(result.stdout)
    except json.decoder.JSONDecodeError:
        rprint('json error')
    except RuntimeError as e:
        rprint(e)

    return data


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
        RuntimeError:
            If command exits with non-zero exit code.
        SystemExit:
            If command not found exits program with exit code 100.

    Returns:
        Object holding data about completed process, including stdout.
    """
    if verbose:
        rprint(' '.join([shlex.quote(arg) for arg in args]))
    try:
        result = subprocess.run(args, capture_output=True, check=True)
    except FileNotFoundError:
        command = args[0]
        rprint(f"Command '{command}' not found on system. Please install.")
        raise SystemExit(100)
    except subprocess.CalledProcessError as e:
        error = e.stderr.decode().strip()
        message = f"Command returned error code {e.returncode}: {error!r}"
        rprint(message)
        raise RuntimeError(message) from None
    return result


class ChapteriserOld:
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
            rprint(f"Output folder already exists: {self.folder}")
            raise SystemExit(1)
        self.folder.mkdir()

        # Output files
        with change_folder(self.folder, verbose=self.verbose):
            for number, chapter in enumerate(self.chapters, 1):
                filename = self.make_filename(chapter)
                rprint(f"[{number}/{self.num_chapters}] {filename}")
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
        average = self.human_time(num_seconds / self.num_chapters)
        rprint(
            f"[cyan]"
            f"Found {self.num_chapters:,} chapters within {total} of audio.\n"
            f"That is an average of {average} per chapter."
            f"[/cyan]"
        )
        folder = self.make_foldername()
        rprint(f"[bright_cyan]Creating folder: '{folder}'")
        lines = []
        for number, chapter in enumerate(self.chapters):
            lines.append(repr(self.make_filename(chapter)))

        columns = Columns(lines, equal=True, expand=True)
        rprint(columns)

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

    def make_filename(
        self,
        chapter: Chapter,
        index: int,
        padding: int = 2,
        suffix: str = 'mp3',
    ) -> str:
        """
        Build filename

        Args:
            index:
                Index of
        """
        return f"{index:0>{padding}}. {chapter.title}.{suffix}"


@dataclass
class Chapter:
    """
    Basic metadata on audiobook clips.
    """
    start: float
    end: float
    title: str


class Chapteriser:
    """
    Plan out the chapters that audiobook should be broken up into.

    The output of this class is only a list of `Chapter` objects which are
    either extracted from the original file's metadata, or built-up manually.

        >>> chapteriser = Chapteriser(path_to_media)
        >>> chapteriser.chapterise()
        [Chapter(start=0, end=1190.5872723555556, title='Part 1'),
         Chapter(start=1190.5872723555556, end=2381.174544711111, title='Part 2'),
         ...
         Chapter(start=51195.25271128892, end=52385.83998364448, title='Part 44'),
         Chapter(start=52385.83998364448, end=53576.42725600004, title='Part 45')]

    Attrs:
        target_minutes:
            If manually creating parts, aim for this many minutes long.
    """

    target_minutes: int = 20

    def __init__(self, path: Path):
        """
        Initialiser.

        Args:
            path:
                Path to media file.
        """
        self.path = path
        self.mediainfo = MediaInfo(self.path)

    def chapterise(self) -> list[Chapter]:
        """
        Create chapters.

        Returns:
            List of chapter instances.
        """
        # Metadata
        pp(self.mediainfo.get_duration())
        chapters = self.mediainfo.get_chapters()

        # Fallback to fixed-sized parts
        if not chapters:
            chapters = self.make_parts()

        return chapters

    def make_parts(self) -> list[Chapter]:
        """
        Create evenly sized parts in the absence of better data.

        Returns:
            List of chapter instances.
        """
        duration = self.mediainfo.get_duration()
        num_parts = round((duration / 60) / self.target_minutes)
        seconds = duration / num_parts

        chapters = []
        start = 0
        end = seconds
        for index in range(1, (num_parts + 1)):
            chapters.append(Chapter(start, end, f"Part {index}"))
            start = end
            end += seconds

        return chapters


class MediaInfo:
    """
    Basic info about media file, powered by `ffprobe` binary.
    """
    def __init__(self, path: Path):
        """
        Initialiser.

        Run's `ffprobe` against given path and collects its output.
        """
        self.path = path
        self.data = ffprobe(self.path)

    def get_duration(self) -> float:
        """
        Total duration of mediafile, in seconds.
        """
        duration = float(self.data['format']['duration'])
        return duration

    def get_chapters(self) -> list[Chapter]:
        """
        Extract chapter metadata directly from media file.

        Audiobooks are often supplied in M4A or M4B formats which is a single
        file, but contain bookmarks for each chapter.

        Returns:
            Possibly empty list of Chapter objects.
        """
        data = self.data.get('chapters', [])
        if not data:
            logger.error('No chapters found in audio file: %s', self.path)
            return []

        chapters = []
        for datum in data:
            start = float(datum['start_time'])
            end = float(datum['end_time'])
            title = datum.get('tags', {}).get('title', '')
            chapters.append(Chapter(start, end, title))

        return chapters


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
    chapteriser = Chapteriser(path)
    pp(chapteriser.chapterise())


    raise SystemExit(0)
    with change_folder(path.parent, verbose=options.verbose):
        chapters = ffprobe_data(path.name, verbose=options.verbose)

    # Check with user
    add_index = 0
    if options.add:
        add_index = int(options.add)
    chapteriser = Chapteriser(path, chapters, add_index=add_index, verbose=options.verbose)
    chapteriser.preview()

    raise SystemExit(0)

    if options.confirm:
        proceed = Confirm.ask("Do you wish to proceed?", default=False)
        pp(proceed)
        raise SystemExit(0)

        rprint("Do you wish to proceed [y/N]?")
        response = input().lower()
        if not response.startswith('y'):
            raise SystemExit(0)
    chapteriser.extract()
    return 0


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    sys.exit(main(options))
