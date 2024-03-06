#!/usr/bin/env python3

"""
Break a single-file audio book into separate MP3 files, one per chapter.

The original file is left untouched. Output files are written into a new
directory in the same folder as the original file.

Requires ffmpeg binaries, the Python package 'colorama', and chapter markers
in the input file.
"""


import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
from pprint import pprint as pp
import re
import shlex
import shutil
import subprocess
import sys

import colorama


class Chapteriser:
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
            Print.error(f"Output folder already exists: {self.folder}")
            raise SystemExit(1)
        self.folder.mkdir()

        # Output files
        with change_folder(self.folder, verbose=self.verbose):
            for number, chapter in enumerate(self.chapters, 1):
                filename = self.make_filename(chapter)
                Print.progress(f"[{number}/{self.num_chapters}] {filename}")
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
        Print.progress(f"Found {self.num_chapters:,} chapters within {total} of audio. ")
        average = self.human_time(num_seconds / self.num_chapters)
        Print.progress(f"That is an average of {average} per chapter.")
        folder = self.make_foldername()
        Print.help(f"Create folder: '{folder}'")
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


class Print:
    @staticmethod
    def it(string, *styles, **kwargs):
        """
        Print string using colorama styles in single operation.

        Many styles can be passed at once. The terminal colours are reset
        after each print operation.

        https://pypi.org/project/colorama/
        """
        parts = [*styles, str(string), colorama.Style.RESET_ALL]
        print(''.join(parts), **kwargs)

    # Styles ###########################
    @staticmethod
    def command(string, prefix=''):
        Print.cyan(f"{prefix}{string}")

    @staticmethod
    def confirm(string):
        Print.yellow(string, end=' ')

    @staticmethod
    def help(string):
        Print.yellow(string)

    @staticmethod
    def progress(string, heading=False):
        if heading:
            string = f"{f' {string} ':.^80}"
        Print.green(string)

    @staticmethod
    def error(string):
        Print.red(string)

    # Colours ##########################
    @staticmethod
    def cyan(string, **kwargs):
        Print.it(string, colorama.Fore.CYAN)

    @staticmethod
    def green(string, **kwargs):
        Print.it(string, colorama.Fore.GREEN, colorama.Style.BRIGHT)

    @staticmethod
    def magenta(string, **kwargs):
        Print.it(string, colorama.Fore.MAGENTA, colorama.Style.BRIGHT)

    @staticmethod
    def red(string, **kwargs):
        Print.it(string, colorama.Fore.RED, colorama.Style.BRIGHT)

    @staticmethod
    def yellow(string, **kwargs):
        Print.it(string, colorama.Fore.YELLOW, colorama.Style.BRIGHT, **kwargs)


@contextmanager
def change_folder(folder, verbose=False):
    """
    Context manager to change working dir, then restore it again.
    """
    old = Path.cwd()
    if verbose:
        Print.command(f"cd {folder}")
    os.chdir(folder)
    yield
    os.chdir(old)


def columnise(strings):
    """
    Return multi-line string containing given strings formatted in columns.
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


def clean(filename):
    """
    Perform best-effort to clean given string into a legal filename.
    Preserves case.
    """
    string = filename.strip()
    # Colon to hyphen
    string = string.replace(':', ' - ')
    # Replace illegal characters
    string = re.sub(r'[^\w\' .,\(\)]', ' ', string)
    # Compact runs of whitespace
    string = re.sub(r'\s+', ' ', string)
    return string


def ffmpeg_extract_audio(path, start, end, output, quality=6, verbose=False):
    """
    Run ``ffmpeg`` to extract audio clip for chapter.
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


def ffprobe_show_chapters(path, verbose=False):
    """
    Run ``ffprobe`` and collect its output.
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


def main(options):
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
        Print.confirm("Do you wish to proceed [y/N]?")
        response = input().lower()
        if not response.startswith('y'):
            raise SystemExit(0)
    chapteriser.extract()


def parse(arguments):
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
    return parser.parse_args()


def run(args, verbose=False):
    """
    Run external command and capture its output.
    """
    if verbose:
        Print.command(' '.join([shlex.quote(arg) for arg in args]))
    try:
        result = subprocess.run(args, capture_output=True, check=True)
    except FileNotFoundError:
        command = args[0]
        Print.error(f"Command '{command}' not found. Please install.")
        raise SystemExit(2)
    except subprocess.CalledProcessError as e:
        error = e.stderr.decode().strip()
        message = f"Command error {e.returncode}: {error!r}"
        Print.error(message)
        raise SystemExit(1)
    return result


if __name__ == '__main__':
    colorama.init()
    options = parse(sys.argv[1:])
    main(options)
    sys.exit(0)
