#!/usr/bin/env python3

"""
RErename

Convenient and safe bulk renamer.

* Performs dry-run by default
* Pre-run check to avoid data loss caused by non-unique output names.

"""

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import sys

import colorama


def parse_arguments(args):
    """
    Create and run `argparse`-based command parser.
    """
    parser = argparse.ArgumentParser(
        description="Bulk rename of files and folders in current directory.")

    # All
    parser.add_argument(
        '-a', '--all', action='store_true', dest='show_all',
        help="show hidden files and folders")

    # Extension
    parser.add_argument(
        '-e', '--extension', action='store_false', dest='preserve_suffix',
        help="file extensions should also be renamed")

    # Force
    parser.add_argument(
        '-f', '--force', action='store_false', dest='dry_run',
        help="actually perform rename operation")

    # Ignore case
    parser.add_argument(
        '-i', '--ignore-case', action='store_true', dest='ignore_case',
        help="Match case insensitively")

    # ~ parser.add_argument(
        # ~ '-s', '--spaces', action='store_true', dest='spaces',
        # ~ help="replace non-alphanumeric characters with spaces")

    # Search
    parser.add_argument(
        'search_regex', metavar='SEARCH', type=str,
        help='use regex with (capturing) patterns')

    # Replace
    parser.add_argument(
        'replace_template', default='',
        metavar='REPLACE', nargs='?', type=str,
        help='literal with (\\1) replacements, leave blank for deletion')

    options = parser.parse_args(args)
    return options


def path_type(path):
    """
    Give the english name for the type of file-system entry given.
    """
    if not path.exists():
        raise FileNotFoundError()
    elif path.is_file():
        return 'file'
    elif path.is_dir():
        return 'folder'
    else:
        return 'entry'


class Colour(Enum):
    BLUE = colorama.Fore.BLUE
    GREEN = colorama.Fore.GREEN
    RED = colorama.Fore.RED + colorama.Style.BRIGHT
    WHITE = colorama.Fore.WHITE
    YELLOW = colorama.Fore.YELLOW + colorama.Style.BRIGHT


class Terminal:
    @staticmethod
    def stderr(string, colour=Colour.WHITE):
        Terminal.print(string, colour, file=sys.stderr)

    @staticmethod
    def print(string, colour=Colour.WHITE, *, file=sys.stdout):
        parts = [colour.value, string, colorama.Style.RESET_ALL]
        print(''.join(parts), file=file)


@dataclass
class RenamerConfiguration:
    """
    Configuration options for the `Renamer` class.
    """
    dry_run: bool
    ignore_case: bool
    preserve_suffix: bool
    replace_template: str
    search_regex: str
    show_all: bool


@dataclass
class Rename:
    """
    A single rename operation.
    """
    old: str                    # Original file name
    new: str                    # New file name
    match_start: int            # Regex match start
    match_end: int              # Regex match end
    replaced: str               # Replaced literal


class RenameError(RuntimeError):
    pass


class Renamer:
    def __init__(self, folder, configuration):
        """
        Initialiser.

        Args:
            folder: `Path` object of folder to examine.
            configuration: `RenamerConfiguration` options.
        """
        self.folder = folder
        self.num_entries = 0
        self.num_matches = 0
        self.config = configuration

    def check(self, renames):
        """
        Ensure that no files will be lost before renaming begins.

        Files can be lost in two main ways:

            1) Destination name overwrites existing file
            2) Destination name is not unique. New file is
               immediately overwritten.

        If either problem is detected, details will be printed and we will
        abort directly.
        """
        seen = {}
        for rename in renames:
            path_new = self.folder / rename.new
            # Existing folder entry
            if path_new.exists():
                message = "Existing {} would be overwritten: {!r}"
                raise RenameError(message.format(path_type(path_new), rename.new))

            # Collisions?
            if rename.new in seen:
                first = seen[rename.new]
                second = rename.old
                message = "Both {!r} and {!r} rename to {!r}"
                raise RenameError(message.format(first, second, rename.new))
            seen[rename.new] = rename.old

    def rename(self):
        """
        Make it happen.
        """
        # Read folder
        paths = self.list()

        # Pre-Calculate renames
        renames = self.calculate(
            paths,
            self.config.search_regex,
            self.config.replace_template,
            self.config.preserve_suffix,
        )

        # Check renames for safety
        try:
            self.check(renames)
        except RenameError as e:
            Terminal.stderr("Aborting: Data-loss detected!", Colour.RED)
            Terminal.stderr(str(e))
            raise SystemExit(2)

        # Finish him!
        self.print_renames(renames)
        if not self.config.dry_run:
            self.execute(renames)
        self.print_summary(renames)

    def execute(self, renames):
        """
        Actually perform the rename operations.
        """
        for rename in renames:
            # Double check that we don't over-write existing file.
            # This should never happen, but... you know.
            new = self.folder / rename.new
            if new.exists():
                message = "Rename target {!r} already exists. Aborting."
                raise RenameError(message.format(new))

            path = self.folder / rename.old
            path.rename(new)

    def print_entry(self, path):
        """
        Print plain directory entry.
        """
        if path.is_dir():
            print(Terminal.blue(path.name) + Terminal.reset() + '/')
        else:
            print(path.name)

    def print_renames(self, renames):
        """
        Highlight the matched part of the orignal string.
        """
        for rename in renames:
            old, start, end = rename.old, rename.match_start, rename.match_end
            parts = []
            add = parts.append

            # Prefix
            add(old[:start])

            # Matched
            add(Colour.RED.value)
            add(old[start:end])
            add(colorama.Style.RESET_ALL)

            # Replaced
            add(Colour.GREEN.value)
            add(rename.replaced)
            add(colorama.Style.RESET_ALL)

            # Suffix
            add(old[end:])

            print(''.join(parts))

    def print_summary(self, renames):
        status = f"{len(renames)} renames from {self.num_entries} entries"
        if self.config.dry_run:
            status += " (DRY RUN)"
        Terminal.stderr(status, Colour.YELLOW)

    def list(self, show_all=False):
        """
        Reads files and folders found in the given folder.

        Args:
            root: `Path` to root folder.
            use_hidden: list entries that start with a period.

        Returns: List of `Path` objects.
        """
        allow_hidden = not self.config.show_all
        entries = []
        for entry in self.folder.iterdir():
            if allow_hidden and entry.name.startswith('.'):
                continue
            entries.append(entry)
        entries.sort()
        return entries

    def calculate(self, paths, search, replace, preserve_suffix):
        """
        Build a list of renames to perform.

        Args:
            entries: Iterable of folder entries
            search: Search regex.
            replace: Replacement template.
            preserve_suffix: Do not do any renames on suffix

        Returns:
            List of 2-tuples. The first element is a `Path` object for a folder
            entry, the second is string with the new name.
        """
        # Flags
        flags = 0
        if self.config.ignore_case:
            flags |= re.IGNORECASE

        # Time to match
        search = re.compile(search, flags=flags)
        self.num_entries = 0
        renames = []
        for path in paths:
            self.num_entries += 1

            # Keep suffix as is?
            if preserve_suffix:
                haystack = path.stem
                suffix = path.suffix
            else:
                haystack = path.name
                suffix = ''

            # Find match
            match = search.search(haystack)
            if match:
                self.num_matches += 1
                start, end = match.span()
                replaced = match.expand(replace)
                new = haystack[:start] + replaced + haystack[end:] + suffix

                # Nothing to do?
                if path.name == new:
                    continue

                renames.append(Rename(path.name, new, *match.span(), replaced))
        return renames


if __name__ == '__main__':
    colorama.init()
    options = parse_arguments(sys.argv[1:])
    config = RenamerConfiguration(**vars(options))
    renamer = Renamer(Path.cwd(), config)
    status = renamer.rename()
    sys.exit(status)
