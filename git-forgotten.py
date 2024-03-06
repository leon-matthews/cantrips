#!/usr/bin/env python3

"""
Find 'git' repos that you've forgotten:

    * Uncommited code
    * Commits that you haven't pushed out
    * Files you've fogetten to add to the repo

TODO Try using  `git status --porcelain` to capture more possible states

"""

import colorama
import contextlib
import logging
import os
import pathlib
import subprocess
import sys


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def chdir(folder):
    """
    Context manager to temporarily change the current working directory.
    """
    curdir = os.getcwd()
    os.chdir(folder)
    yield
    os.chdir(curdir)


def git_repo_folders(parent):
    """
    Yield folders containing git repos.

    This is any regular folder containing in turn the folder '.git'.
    """
    logger.debug("Looking for Git repos under: %s", parent)
    parent = str(parent)
    examined = 0
    found = 0
    for root, dirs, files in os.walk(parent, topdown=True):
        examined += 1
        dirs.sort()
        if '.git' in dirs:
            found += 1
            yield root
            # Don't look any further inside repo
            dirs.clear()
    logger.info(f"{found:,} Git repos found. {examined:,} folders searched.")


class MultiGit:
    def __init__(self, parent, args):
        """
        Initialiser.
        """
        self.args = args if args else ['diff', '--stat', '--color']
        self.encoding = 'utf-8'
        self.parent = self.clean_path(parent)

    def clean_path(self, path):
        """
        Return cannonical version of given path.
        """
        path = pathlib.Path(path)
        path = path.expanduser()
        path = path.resolve()
        return path

    def run(self):
        """
        Main script.
        """
        for repo in git_repo_folders(self.parent):
            with chdir(repo):
                self.run_git(repo, self.args)

    def run_git(self, repo, args):
        """
        Run git 'inside' repo and print output, if not empty.
        """
        args = ['git'] + args
        process = subprocess.run(args, stdout=subprocess.PIPE)
        if process.stdout:
            heading = f"{repo:<80}"
            print(colorama.Style.BRIGHT + colorama.Back.BLUE + heading)
            stdout = process.stdout.decode(self.encoding).strip()
            print(stdout)
            print()


def setup_logging(level):
    logging.basicConfig(
        datefmt="%d-%b-%Y %H:%M:%S",
        format='{message}',
        level=level,
        style='{'
    )


if __name__ == '__main__':
    colorama.init(autoreset=True)
    setup_logging(logging.INFO)

    if len(sys.argv) > 1:
        parent = sys.argv[1]
    else:
        logger.debug('No parent folder given, defaulting to home folder.')
        parent = '~'

    args = sys.argv[2:]

    multi_git = MultiGit(parent, args)
    sys.exit(multi_git.run())
