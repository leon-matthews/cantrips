#!/usr/bin/env python3

"""
Clean-up build files from Rust projects under the current, or given, folder.

Runs the shell command `cargo clean` in every Rust project that needs it. If a
folder contains a folder called 'target' AND a file called `Cargo.toml` it
is considered to need cleaning.
"""

import contextlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator


@contextlib.contextmanager
def chdir(folder: Path) -> Iterator[None]:
    """
    Context manager to temporarily change the current working directory.

    Note that as of Python 3.11 the standard library provides its own
    `contextlib.chdir()`, which does the same thing.
    """
    curdir = os.getcwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(curdir)


def clean(folder: Path) -> str:
    """
    Run the 'cargo clean' command in the given folder.
    """
    with chdir(folder):
        args = ['cargo', 'clean']
        result = subprocess.run(args, capture_output=True, check=True, text=True)
        message = result.stderr.strip()
        return message


def find_unclean(root: Path) -> Iterator[Path]:
    """
    Recursive generator over folders found under given root.

    Args:
        root:
            Folder to start searching from.
    """
    ignored_folders = ('.git', 'src', 'target')
    for (dirpath, dirnames, filenames) in root.walk():
        # Don't recurse in ignored folders
        if dirpath.name in ignored_folders:
            dirnames.clear()
            continue

        # Projects contain a file called 'Cargo.toml' and a folder called 'target'
        if 'target' in dirnames and 'Cargo.toml' in filenames:
            yield dirpath


def main(root: Path) -> int:
    for project in find_unclean(root):
        message = clean(project)
        folder = os.path.relpath(project, root)
        print(f"Found {folder}/: {message}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    root = Path.cwd()
    sys.exit(main(root))
