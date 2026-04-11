#!/usr/bin/python3
"""
Project number from branch name prefixed to commit message, eg. 'PROJECT-1234'

If the script exits with a non-zero error code the commit is aborted.
Git calls script with from one to three arguments. The first is always present
and is the path to the temporary file containing the commit message.

See:
    https://git-scm.com/docs/githooks
"""

from pathlib import Path
import re
import sys
from subprocess import check_output


PREFIX_REGEX = re.compile(r"(PROJECT-\d+)", re.I)


def extract_prefix(branch: str) -> str|None:
    prefix = None
    if matches := PREFIX_REGEX.match(branch):
        prefix = matches.group(0)
    return prefix


def get_branch_name() -> str:
    branch = check_output(["git", "symbolic-ref", "--short", "HEAD"], text=True)
    return branch.strip()


def write_prefix(path: Path, prefix: str) -> None:
    with open(path, "r+") as f:
        message = f.read()
        if message.startswith(prefix):  # Don't write prefix twice
            return
        f.seek(0, 0)
        f.write(f"{prefix} {message}")


def main() -> int:
    try:
        branch = get_branch_name()
        prefix = extract_prefix(branch)
        if prefix is not None:          # Do nothing if branch regex fails
            path = Path(sys.argv[1])
            write_prefix(path, prefix)
    except Exception as e:
        message = "Error running 'prepare-commit-msg' hook script"
        print(f"{message}:\n{e}", file=sys.stderr)
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
