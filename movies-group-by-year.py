#!/usr/bin/env python3
"""
Group movie folders into years.

For example:

    $ ls
    Brazil (1985)/
    Dune (1984)/
    Ghostbusters (1984)/
    Threads (1984)/
    Weird Science (1985)/

    $ ./movies_group_by_year.py
    $ ls
    1984/ ...3 items
    1985/ ...2 items

"""

import argparse
import collections
from pathlib import Path
import re
import sys
from typing import Optional

from pprint import pprint as pp


def find_movie_folders(root: Path) -> dict[int, list[Path]]:
    """
    Find folders matching expected name pattern .

    Movies folders have the name of the movie, a space, then the full year in
    parentheses. For example: "1984 (1984)"
    """
    movies = collections.defaultdict(list)
    for path in root.iterdir():
        year = extract_year(path)
        if year is not None:
            movies[year].append(path)
    return movies


def extract_year(path: Path) -> Optional[int]:
    """
    Extract the movie's year or return None.
    """
    if not (match := re.search(r"\((\d\d\d\d)\)$", path.name)):
        return None

    year = int(match.group(1))
    return year


def from_subfolders(root: Path) -> tuple[int, int]:
    """
    Move movie folders into a single folder

    Intended to exactly reverse the actions taken by `into_subfolder().`

    Returns:
        Count of folders moved, year folders deleted.
    """
    def is_year(name):
        return bool(match := re.match(r"\d\d\d\d", name))

    count = 0
    for path in root.iterdir():
        # Move items out of 'year' folders.
        if not is_year(path.name):
            continue

        for subpath in path.iterdir():
            subpath.rename(path.parent / subpath.name)
            count += 1

        # Remove empty year folders
        path.rmdir()

    return count


def into_subfolders(root: Path) -> tuple[int, int]:
    """
    Move the given movies into year-based subfolders.

    Args:
        root:
            Base folder containing movie folders.

    Returns:
        Counts of folders moved, year folders created.
    """
    movies = find_movie_folders(root)
    count = 0
    for year in movies:
        # Create folder for year?
        destination = root / str(year)
        if not destination.exists():
            destination.mkdir()

        # Movie movies
        folders = movies[year]
        for folder in folders:
            folder.rename(destination / folder.name)
            count += 1

    return count


def main(options: argparse.Namespace) -> int:
    # Root
    root = Path(options.root).resolve()
    if not root.is_dir():
        print(f"Given root is not a folder: {root}", file=sys.stderr)
        raise SystemExit(1)

    # Are you sure though?
    if options.confirm:
        print("Moving folders around could result in data loss.", file=sys.stderr)
        print("Are you sure [y/N]?", end=' ', file=sys.stderr)
        response = input().lower()
        if not response.startswith('y'):
            raise SystemExit(0)

    # Run!
    if options.reverse:
        num_moved = from_subfolders(root)
    else:
        num_moved = into_subfolders(root)
    print(f"{num_moved:,} folders moved", file=sys.stderr)

    return 0


def parse(arguments: list[str]) -> argparse.Namespace:
    description = "Group movie folders into years."
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        '-r', '--reverse', action='store_true', dest='reverse',
        help="reverse operation and restore folders")
    parser.add_argument(
        '-v', '--verbose', action='store_true', dest='verbose',
        help="show verbose output")
    parser.add_argument(
        '-y', '--yes', action='store_false', dest='confirm',
        help="assume yes; do not ask for confirmation")
    parser.add_argument('root', metavar='ROOT', help="path to folders' root")
    options = parser.parse_args()
    return options


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    try:
        code = main(options)
    except Exception as e:
        print(f"Unexpected error: {e.__class__.__name__}: {e}", file=sys.stderr)
        if options.verbose:
            raise
        code = 255
    sys.exit(code)
