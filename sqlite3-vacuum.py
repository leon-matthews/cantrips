#!/usr/bin/env python3

"""
Safely create backup copies of every SQLite3 database found under given root.

The SQLite 'Online Backup API' is used, as per: https://www.sqlite.org/backup.html
The backup copy is created in the same folder as the original, simply with the
suffix  '.old' added. For good measure, VACUUM and ANALYZE are run on the original
database before the backup copy is created.
"""

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import subprocess
import sys


logger = logging.getLogger()


@contextmanager
def cd(folder):
    """
    Context manager to change local directory.
    """
    previous = Path.cwd()
    logger.debug("cd %s", folder)
    os.chdir(str(folder))
    try:
        yield
    finally:
        logger.debug("cd %s", previous)
        os.chdir(str(previous))


def data_folders(root):
    """
    Return sorted list of data folders inside the given project root.

    Best illustrated by example:

        >>> list(data_folders('~/Projects'))
        [PosixPath('~/Projects/example.co.nz/data'),
         PosixPath('~/Projects/example.com/data')]

    """
    root = Path(root)
    folders = []
    assert root.is_dir(), "Root must be an existing folder"
    for path in root.iterdir():
        if path.is_dir():
            data = path / 'data'
            if data.is_dir():
                folders.append(data)
    folders.sort()
    return folders


def run(command):
    """
    Run externaml command, abort on command failure.
    """
    logger.debug(command)
    return subprocess.run(command, check=True, shell=True)


def sqlite3_files(folder):
    """
    Yield '*.sqlite3' files from given directory.
    """
    for path in folder.iterdir():
        if path.is_file():
            if path.suffix == '.sqlite3':
                yield path


def backup(db):
    """
    Create safe copy of SQLite3 database, using its own backup API.
    """
    # Be careful not to overwrite previous backup until the
    # new backup has been sucessfully created.
    backup = Path("{}.old".format(db.name))
    previous_backup = Path("{}.older".format(db.name))
    if backup.exists():
        logger.debug("mv {} {}".format(backup, previous_backup))
        backup.rename(previous_backup)

    # Create backup
    run('sqlite3 {} ".backup \'{}\'"'.format(db.name, backup.name))

    # Delete previous backup
    if previous_backup.exists():
        logger.debug("rm %s", previous_backup)
        previous_backup.unlink()


def vacuum(db_file):
    """
    Do a full tidy-up of SQLite database
    """
    command = "sqlite3 {} ".format(db_file.name)
    run(command + 'ANALYZE;')
    run(command + 'VACUUM;')


def main(root):
    """
    Tidy up and backup every SQLite database found across all projects.
    """
    for folder in data_folders(root):
        project = folder.parent.name
        logger.info("Backup database for %s", project)
        for db_file in sqlite3_files(folder):
            with cd(db_file.parent):
                vacuum(db_file)
                backup(db_file)
    return 0


if __name__ == '__main__':
    if not len(sys.argv) == 2:
        print("usage: {} ROOT".format(sys.argv[0]))
        sys.exit(1)
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    root = sys.argv[1]
    sys.exit(main(root))
