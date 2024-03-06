#!/usr/bin/env python3

import argparse
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
from pprint import pprint as pp
import re
import subprocess
import sys
from typing import Iterable, Optional


@contextlib.contextmanager
def chdir(folder: Path) -> None:
    """
    Context manager to temporarily change the current working directory.
    """
    curdir = os.getcwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(curdir)


def existing_folder(string: str) -> Path:
    """
    An `argparse` type to convert string to a `Path` object.

    Raises:
        argparse.ArgumentTypeError:
            If path is not an existing folder.

    Returns:
        Path to an existing folder.
    """
    path = Path(string).expanduser().resolve()
    error = None
    if not path.exists():
        error = f"Folder does not exist: {path}"
    if not path.is_dir():
        error = f"Path is not a folder: {path}"

    if error is not None:
        raise argparse.ArgumentTypeError(error)
    return path


def find_repos(parent: Path, *, find_hidden=False) -> Iterable[Path]:
    """
    Yield folders containing git repos.

    This is any regular folder containing in turn the folder '.git'.

    Args:
        parent:
            Folder to look under.
        find_hidden:
            Look inside folders starting with a full-stop.

    Returns:
        Generator over folder Path objects
    """
    parent = Path(parent)
    examined = 0
    found = 0
    for root, dirs, files in os.walk(parent, topdown=True):
        # Skip hidden folders
        if os.path.basename(root).startswith('.'):
            dirs.clear()
            continue

        examined += 1
        dirs.sort()
        if '.git' in dirs:
            found += 1
            yield Path(root)
            # Don't look any further inside repo
            dirs.clear()

    pp(examined)


@dataclass
class Commit:
    timestamp: int                      # Unix timestamp
    author: str                         # eg. "Name <email>"
    project: str                        # Top-level folder name, eg. "lost.co.nz"
    message: str                        # First line only

    @classmethod
    def from_log(cls, line: str):
        """
        Construct object from git log line.
        """
        # TODO


class GitLog:
    def __init__(self):
        """
        Run 'git log' in a project folder and parse its output.
        """
        self.regex = re.compile(
            r"(\d+) "                   # Unix Epoch
            r"(.*>) "                   # Name <email>
            r"(.*)"                     # Commit message
        )

    def run(self, folder:  Path, since: Optional[int] = None) -> str:
        """
        Run `git log` in current directory and capture its output.

        Args:
            folder:
                Top-level git project folder.
            since:
                Optionally exclude commits before this Unix timestamp.

        Returns:
            Multiline unicode string, one-line per commit.
        """
        args = [
            'git',
            'log',
            '--pretty=%at %aN <%aE> %s',
        ]
        if since is not None:
            args.append(f"--since={since}")
        with chdir(folder):
            process = subprocess.run(args, capture_output=True, text=True, timeout=5.0)
        return process.stdout

    def parse(self, line) -> list[str]:
        """
        Break log line into parts.
        """
        match = self.regex.match(line)
        if match is None:
            raise ValueError(f"Could not parse git log: {line!r}")

        pp(self.regex.match(line).groups())


def main(options: argparse.Namespace) -> int:
    log = GitLog()
    for project in find_repos(options.folder):
        output = log.run(project, since=1_695_000_000)
        if output:
            pp(project)
            pp(output)
            pp('')

    return 0

    print(output)
    print(repr(output))
    for line in output.splitlines():
        print(repr(line))
        print(log.parse(line))
    return 0


def parse(args: list[str]) -> argparse.Namespace:
    description = "Summarise recent Git project activity"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'folder',
        default='~',
        metavar='FOLDER',
        nargs='?',
        type=existing_folder,
        help='folder to look under',
    )
    return parser.parse_args()


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    sys.exit(main(options))
