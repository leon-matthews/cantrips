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

"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from typing import Any

from rich import print as rprint
from rich.columns import Columns
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm


logger = logging.getLogger(__name__)


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

    Returns:
        Subprocess completed process.
    """
    args = [
        'ffmpeg',
        '-nostdin',
        '-i', str(path),
        '-hide_banner',
        '-vn', '-sn', '-dn',    # Drop video, subtitle and data streams
        '-ss', f"{start:.3f}",
        '-to', f"{end:.3f}",
        '-codec:a',
        'libmp3lame',
        '-ac', '2',
        '-qscale:a', str(quality),
        '-f', 'mp3',
        '-n',                   # Don't overwrite existing
        str(output),
    ]
    result = run(args)
    return result


def ffprobe(path: Path) -> dict[str, Any]:
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

    data: dict[str, Any] = {}
    try:
        result = run(args)
        parsed = json.loads(result.stdout)
        if not isinstance(parsed, dict):
            raise RuntimeError("ffprobe returned non-object JSON")
        data = parsed
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


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """
    Run external command and capture its output.

    Thin wrapper around `subprocess.run()`.

    Args:
        args:
            Command and its arguments.

    Raises:
        RuntimeError:
            If command exits with non-zero exit code.
        SystemExit:
            If command not found exits program with exit code 100.

    Returns:
        Object holding data about completed process, including stdout.
    """
    logger.info(' '.join([shlex.quote(arg) for arg in args]))
    try:
        result = subprocess.run(
            args, capture_output=True, check=True, text=True, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        command = args[0]
        logger.error(f"Command '{command}' not found on system. Please install.")
        raise SystemExit(100)
    except subprocess.CalledProcessError as e:
        error = e.stderr.strip()
        message = f"Command returned error code {e.returncode}: {error!r}"
        logger.error(message)
        raise RuntimeError(message) from None
    return result


@dataclass(frozen=True)
class Chapter:
    """
    Basic metadata on audiobook clips.
    """
    start: float
    end: float
    title: str


class ChapterPlanner:
    """
    Plan out the chapters that audiobook should be broken up into.

    The output of this class is only a list of `Chapter` objects which are
    either extracted from the original file's metadata, or built-up manually.

        >>> planner = ChapterPlanner(path_to_media)
        >>> planner.plan()
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

    def __init__(self, path: Path, start: int = 1):
        """
        Initialiser.

        Args:
            path:
                Path to media file.
            start:
                Number to start counting from, only if chapter names not found
                in input file.
        """
        self.start = start
        self.mediainfo = MediaInfo(path)
        self.duration = self.mediainfo.get_duration()

    def plan(self) -> list[Chapter]:
        """
        Create chapters.

        Returns:
            List of chapter instances.
        """
        chapters = self.mediainfo.get_chapters()
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
        num_parts = max(1, round((self.duration / 60) / self.target_minutes))
        seconds = self.duration / num_parts

        chapters = []
        start = 0.0
        end = seconds
        for index in range(0, num_parts):
            name = f"Part {index + self.start}"
            chapters.append(Chapter(start, end, name))
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
            logger.warning("No chapters found in audio file: %r", self.path.name)
            return []

        chapters = []
        for datum in data:
            start = float(datum['start_time'])
            end = float(datum['end_time'])
            title = datum.get('tags', {}).get('title', '')
            chapters.append(Chapter(start, end, title))

        chapters.sort(key=lambda c: c.start)
        return chapters


class ClipNamer:
    """
    Build filenames and folder name for the output clips.
    """
    def __init__(self, start: int, num_chapters: int):
        """
        Initialiser.

        Args:
            start:
                Number to start counting from for the filename prefix.
            num_chapters:
                Total chapter count, used to size the zero-padding.
        """
        self.start = start
        max_index = (num_chapters - 1) + start
        self.padding = self.calculate_padding(max_index)

    def filename(
        self,
        index: int,
        chapter: Chapter,
        suffix: str = 'mp3',
    ) -> str:
        """
        Build a single filename.

        Args:
            index:
                Index into chapters list.
            chapter:
                Chapter instance.

        Returns:
            Bare-string filename.
        """
        prefix = index + self.start
        name = f"{prefix:0>{self.padding}}. {chapter.title.strip()}.{suffix}"
        return clean_filename(name)

    def foldername(self, media_stem: str) -> str:
        """
        Build the output folder name from the source file's stem.
        """
        return clean_filename(media_stem)

    @staticmethod
    def calculate_padding(max_value: int) -> int:
        """
        Calculate the width of padding required for file names.

            >>> ClipNamer.calculate_padding(33)
            2
            >>> ClipNamer.calculate_padding(1000)
            4

        Args:
            max_value:
                Highest number required.

        Returns:
            Number of padding digits required.
        """
        return max(2, math.ceil(math.log(max_value + 1, 10)))


class ClipWriter:
    """
    Extract audio clips from the source file into an output folder.
    """
    def __init__(self, media_path: Path, folder: Path):
        """
        Initialiser.

        Args:
            media_path:
                Path to the source media file.
            folder:
                Path to the output folder clips will be written into.
        """
        self.media_path = media_path
        self.folder = folder

    def create_folder(self) -> None:
        """
        Create parent folder for clips to live in.

        Raises:
            RuntimeError:
                If folder already exists.
        """
        if self.folder.exists():
            message = f"Output folder already exists: '{self.folder}'"
            logger.error(message)
            raise RuntimeError(message)

        self.folder.mkdir()

    def write(self, chapter: Chapter, output_path: Path) -> None:
        """
        Extract a single chapter to the given output path.

        The clip is written to a ``.part`` file and atomically renamed on
        success, so an interrupted run leaves partial clips clearly marked.

        Args:
            chapter:
                Chapter to extract from the source file.
            output_path:
                Final path the clip should land at.
        """
        partial = output_path.with_name(output_path.name + '.part')
        ffmpeg_extract_audio(self.media_path, chapter.start, chapter.end, partial)
        partial.rename(output_path)


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
        '-j', '--jobs',
        action='store',
        default=max(1, (os.cpu_count() or 2) // 2),
        metavar='NUM',
        type=int,
        help="number of ffmpeg jobs to run in parallel (default: half of CPU cores)")
    parser.add_argument(
        '-s', '--start',
        action='store',
        default=1,
        metavar='NUM',
        type=int,
        help="Don't start numbering from one")
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help="print commands as they are run")
    parser.add_argument(
        '-y', '--yes', action='store_false', dest='confirm',
        help="assume yes; do not ask for confirmation")
    parser.add_argument('path', metavar='PATH', help='audio file to process')
    options = parser.parse_args()
    if options.jobs < 1:
        parser.error("--jobs must be at least 1")
    return options


def preview(
    chapters: list[Chapter],
    namer: ClipNamer,
    folder: Path,
    duration: float,
) -> None:
    """
    Preview, then ask user for permission to continue.

    Nothing is returned, no side effects except for a possible system exit.

    Raises:
        SystemExit:
            If user chose not to continue.
    """
    filenames = [namer.filename(index, ch) for index, ch in enumerate(chapters)]
    total = Seconds(duration)
    average = total / len(filenames)
    rprint(f"Creating {len(filenames)} files averaging {average} each,")
    rprint(f"a total of {total} of audio:")
    rprint(f":file_folder: {folder.name}/")

    columns = Columns(filenames, column_first=True, equal=True, expand=True, padding=(0, 2))
    rprint(columns)
    rprint()

    proceed = Confirm.ask("Do you wish to proceed?", default=True)
    if not proceed:
        raise SystemExit(0)


def run_encode(
    writer: ClipWriter,
    namer: ClipNamer,
    chapters: list[Chapter],
    jobs: int,
) -> None:
    """
    Encode each chapter into its own clip, with up to `jobs` ffmpeg jobs in parallel.

    Raises:
        KeyboardInterrupt:
            Re-raised after cancelling pending futures so the caller can clean up.
    """
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    )
    description = f"Encoding ({jobs} job{'s' if jobs != 1 else ''})"
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [
            executor.submit(writer.write, chapter, writer.folder / namer.filename(index, chapter))
            for index, chapter in enumerate(chapters)
        ]
        try:
            with progress:
                task = progress.add_task(description, total=len(chapters))
                for future in as_completed(futures):
                    future.result()
                    progress.update(task, advance=1)
        except KeyboardInterrupt:
            for f in futures:
                f.cancel()
            raise


def main(options: argparse.Namespace) -> int:
    """
    Command's entry point.

    Args:
        options:
            Command-line options parsed by `parse()`.

    Returns:
        Integer error code.
    """
    try:
        path = Path(options.path).resolve()
        planner = ChapterPlanner(path, options.start)
        chapters = planner.plan()
        namer = ClipNamer(start=options.start, num_chapters=len(chapters))
        folder = path.parent / namer.foldername(path.stem)
        writer = ClipWriter(media_path=path, folder=folder)
    except RuntimeError as e:
        rprint(e)
        return 1

    if options.confirm:
        preview(chapters, namer, folder, planner.get_duration())

    jobs = min(options.jobs, len(chapters))
    try:
        writer.create_folder()
        run_encode(writer, namer, chapters, jobs)
    except RuntimeError as e:
        rprint(e)
        return 1
    except KeyboardInterrupt:
        rprint("\n[yellow]Interrupted.[/yellow]")
        return 130

    return 0


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if options.verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(show_path=False, show_time=False)]
    )
    sys.exit(main(options))
