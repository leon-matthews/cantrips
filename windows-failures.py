#!/usr/bin/env python3
"""
Scan a directory tree for names that Windows can't use.

Flags file and directory names containing Windows/SMB-reserved characters,
trailing dots or spaces, control characters, or reserved DOS device names.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator

# Characters legal on Linux but reserved in Windows/SMB filenames.
RESERVED_CHARS = frozenset('<>:"|?*\\')

# Base names Windows forbids regardless of any extension.
RESERVED_DOS_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def reasons_for(name: str) -> list[str]:
    """
    Return the problems with a single path component, empty if it is clean.

    The name must be a basename, never a full path.
    """
    found: list[str] = []

    bad = sorted(c for c in set(name) if c in RESERVED_CHARS)
    if bad:
        found.append(f"reserved character(s): {' '.join(bad)}")

    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        found.append("control character(s)")

    if name != name.lstrip():
        found.append("leading whitespace")

    if name != name.rstrip():
        found.append("trailing whitespace")

    if name.rstrip().endswith("."):
        found.append("trailing dot")

    if name.split(".", 1)[0].upper() in RESERVED_DOS_NAMES:
        found.append("reserved device name")

    return found


def display(path: str) -> str:
    """
    Return a printable form of a path that may hold undecodable bytes.
    """
    return path.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def walk(root: str) -> Iterator[tuple[str, bool]]:
    """
    Yield (path, is_dir) for every entry under root; symlinks are not followed.
    """

    def on_error(exc: OSError) -> None:
        print(f"warning: {display(str(exc))}", file=sys.stderr)

    walker = os.walk(root, onerror=on_error, followlinks=False)
    for dirpath, dirnames, filenames in walker:
        for name in dirnames:
            yield os.path.join(dirpath, name), True
        for name in filenames:
            yield os.path.join(dirpath, name), False


def scan(root: str) -> int:
    """
    Walk root, print each flagged entry sorted by path, return the count.
    """
    findings: list[tuple[str, bool, list[str]]] = []
    for path, is_dir in walk(root):
        problems = reasons_for(os.path.basename(path))
        if problems:
            findings.append((path, is_dir, problems))

    for path, is_dir, problems in sorted(findings, key=lambda f: f[0]):
        kind = "DIR " if is_dir else "FILE"
        print(f"{kind}  {display(path)}")
        print(f"      reasons: {'; '.join(problems)}")

    return len(findings)


def main() -> int:
    """
    Parse arguments, scan the tree, and print a one-line summary.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="directory tree to scan")
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"error: not a directory: {args.root}", file=sys.stderr)
        return 2

    count = scan(args.root)
    print(f"\n{count} problematic name(s) found.", file=sys.stderr)
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
