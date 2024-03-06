#!/usr/bin/env python3

"""
Show a daily log of activity accross all of your local git repos.
"""

import argparse
import colorama
from collections import namedtuple
import contextlib
import datetime
import logging
import os
import pathlib
from pprint import pprint as pp
import re
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
    logger.info("{:,} Git repos found. {:,} folders searched.".format(found, examined))


class MultiGit:
    def __init__(self, parent, args):
        """
        Initialiser.
        """
        self.args = args if args else ['diff', '--numstat', '--color']
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
            heading = "{:<80}".format(repo)
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


Numstat = namedtuple('Numstat', 'name email timestamp files_changed insertions deletions')


class ParseError(RuntimeError):
    pass


class ParseNumStat:
    def parse(self, string):
        for commit in self.commits(string):
            record = self.make_record(commit)
            yield record

    def commits(self, string):
        """
        Break full log string into individual commit strings.
        """
        parts = re.split(r'commit [0-9a-f]{40}', string)
        for commit in parts:
            commit = commit.strip()
            if commit:
                yield commit

    def get_name(self, lines):
        """
        Pull name and email address out of
        """
        prefix = 'Author: '
        for line in lines:
            if line.startswith(prefix):
                line = line.strip(prefix)
                pattern = r'([^<]*) <([^>]*)>'
                match = re.match(pattern, line)
                if match is None:
                    self.prefix_error(pattern, line)
                return match.groups()
        self.prefix_error(prefix, lines)

    def get_timestamp(self, lines):
        prefix = 'Date:   '
        for line in lines:
            if line.startswith(prefix):
                line = line.strip(prefix)
                timestamp = datetime.datetime.strptime(line, "%Y-%m-%d %H:%M:%S %z")
                return timestamp
        self.prefix_error(prefix, lines)

    def get_changes(self, lines):
        pp(lines)
        return (12, 234, 45)

    def make_record(self, commit):
        lines = commit.splitlines()
        name, email = self.get_name(lines)
        timestamp = self.get_timestamp(lines)
        files_changed, insertions, deletions = self.get_changes(lines)
        return Numstat(name, email, timestamp, files_changed, insertions, deletions)

    def prefix_error(self, prefix, lines):
        lines = '\n'.join(lines)
        raise ParseError(f"Prefix {prefix!r} not found in: {lines!s}")

    def regex_error(self, regex, line):
        raise ParseError(f"No match for '{regex!s}' found in: {line!r}")


if __name__ == '__main__':
    with open('output.txt') as f:
        OUTPUT = f.read()

    parser = ParseNumStat()
    for record in parser.parse(OUTPUT):
        pp(record)
        break


    sys.exit(0)

    colorama.init(autoreset=True)
    setup_logging(logging.INFO)

    if len(sys.argv) > 1:
        parent = sys.argv[1]
    else:
        logger.debug('No parent folder given, defaulting to home folder.')
        parent = '~'

    delta = datetime.timedelta(days=3)
    date = datetime.date.today() - delta
    date = date.isoformat()
    args = [
        "log", "--all", "--numstat", "--color=always",
        f"--after='{date} 00:00:00'",
        f"--before='{date} 23:59:59'",
    ]

    multi_git = MultiGit(parent, args)
    sys.exit(multi_git.run())
