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
import re
import shlex
import subprocess
import sys
from typing import Any, Iterator, Union

from rich import print as rprint
from rich.columns import Columns
from rich.logging import RichHandler
from rich.pretty import pprint as pp
from rich.prompt import Confirm


logger = logging.getLogger(__name__)


@contextmanager
def change_folder(folder: Path, verbose: bool = False) -> Iterator[None]:
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


def clean_filename(filename: str) -> str:
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
) -> subprocess.CompletedProcess[str]:
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


def ffprobe(path: Path, verbose: bool = False) -> dict[str, Any]:
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

    Raises:
        RuntimeError:
            If something goes wrong running command.

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
        message = f"Could not decode JSON output: {result.stdout!r}"
        logger.error(message)
        raise RuntimeError("Invalid output from ffprobe")
    except RuntimeError as e:
        logger.error("Error running ffprobe: %s", e)
        raise

    return data


class Seconds:
    """
    Attach useful methods to a floating-point quantity of seconds.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds

    def human_duration(self) -> str:
        """
        Rough-and-ready formatted duration phrase.

        eg. '1 hour and 15 minutes'
        """
        hours, minutes, _ = self.split()
        hours_part = f"{hours} hour" if hours == 1 else f"{hours} hours"
        minutes_part = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
        if hours > 0:
            return f"{hours_part} and {minutes_part}"
        else:
            return f"{minutes_part}"

    def treble(self, truncate:bool = False) -> str:
        """
        Build HH:MM:SS style duration string.

        For example::

            >>> Seconds(45_930).treble()
            '12:45:30.0'
            >>> Seconds(45_930).treble(truncate=True)
            '12:45:30'

        Args:
            truncate:
                If true, the seconds value is truncated to an integer

        Returns:
            Formatted string denoting duration.
        """
        hours, minutes, seconds = self.split()
        time = f"{hours:0>2}:{minutes:0>2}:{int(seconds):0>2}"
        if not truncate:
            time += str(seconds - int(seconds))[1:]
        return time

    def split(self) -> tuple[int, int, float]:
        """
        Break seconds into hours, minutes, and remaining seconds.
        """
        hours, seconds = divmod(self.seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return int(hours), int(minutes), float(seconds)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.seconds})"

    def __str__(self) -> str:
        return self.human_duration()

    def __truediv__(self, other: float|Seconds) -> Seconds:
        if isinstance(other, (float, int)):
            return Seconds(self.seconds / other)
        elif isinstance(other, Seconds):
            return Seconds(self.seconds / other.seconds)
        else:
            return NotImplemented


def run(args: list[str], verbose: bool = False) -> subprocess.CompletedProcess[str]:
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
        result = subprocess.run(args, capture_output=True, check=True, text=True)
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
        self.duration = self.mediainfo.get_duration()

    def chapterise(self) -> list[Chapter]:
        """
        Create chapters.

        Returns:
            List of chapter instances.
        """
        # From metadata?
        chapters = self.mediainfo.get_chapters()

        # Fallback to fixed-sized parts
        if not chapters:
            chapters = self.make_parts()

        return chapters

    def get_duration(self) -> float:
        """
        Total length of audiobook, in seconds.
        """
        return self.duration

    def make_parts(self) -> list[Chapter]:
        """
        Create evenly sized parts in the absence of better data.

        Returns:
            List of chapter instances.
        """
        num_parts = round((self.duration / 60) / self.target_minutes)
        seconds = self.duration / num_parts

        chapters = []
        start = 0.0
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
            logger.warning('No chapters found in audio file: %s', self.path)
            return []

        chapters = []
        for datum in data:
            start = float(datum['start_time'])
            end = float(datum['end_time'])
            title = datum.get('tags', {}).get('title', '')
            chapters.append(Chapter(start, end, title))

        return chapters


class Splitinator:
    """
    Split single-file audiobook into seperate files.
    """
    def __init__(self, chapteriser: Chapteriser):
        """
        Initialiser.

        Args:
            chapteriser:
                Valid Chapteriser instance.
        """
        self.chapters = chapteriser.chapterise()
        self.path = chapteriser.path

    def calculate_padding(self, max_value: int) -> int:
        """
        Calculate the width of padding required for file names.

            >>> padding_digits(33)
            2
            >>> padding_digits(1000)
            4

        Args:
            max_value:
                Highest number required.

        Return:
            Number of padding digits required.
        """
        padding = max(2, math.ceil(math.log(max_value + 1, 10)))
        return padding

    def extract(self):
        # Output folder
        self.folder = self.path.parent / self.make_foldername()
        if self.folder.exists():
            logger.critical(f"Output folder already exists: {self.folder}")
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

    def make_filename(
        self,
        index: int,
        chapter: Chapter,
        padding: int = 2,
        suffix: str = 'mp3',
    ) -> str:
        """
        Build filename

        Args:
            index:
                Index of
        """
        name = f"{index:0>{padding}}. {chapter.title}.{suffix}"
        name = clean_filename(name)
        return name

    def make_filenames(self) -> list[str]:
        filenames = []
        padding = self.calculate_padding(len(self.chapters))
        for index, chapter in enumerate(self.chapters, 1):
            name = self.make_filename(index, chapter, padding)
            filenames.append(name)
        return filenames

    def make_foldername(self) -> str:
        folder = clean_filename(self.path.stem)
        return folder


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
    # ~ parser.add_argument(
        # ~ '-a', '--add', action='store', metavar='NUM',
        # ~ help='number to add to track index')
    # ~ parser.add_argument(
        # ~ '-v', '--verbose', action='store_true',
        # ~ help="print commands as they are run")
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
    splitinator = Splitinator(chapteriser)

    # Preview, then confirm
    rprint(f":file_folder: {splitinator.make_foldername()}/")
    filenames = splitinator.make_filenames()
    columns = Columns(filenames, equal=True, expand=True)
    rprint(columns)

    total = Seconds(chapteriser.get_duration())
    average = total / len(filenames)
    rprint()
    rprint(
        f"Creating {len(filenames)} files averaging {average} each, "
        f"totalling {total} of audio."
    )

    if options.confirm:
        proceed = Confirm.ask("Do you wish to proceed?", default=False)
    else:
        proceed = True

    if not proceed:
        raise SystemExit(0)

    rprint("DENNO")
    sys.exit(1)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(show_path=False, show_time=False)]
    )
    options = parse(sys.argv[1:])
    sys.exit(main(options))
