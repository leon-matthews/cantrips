#!/usr/bin/env python3
"""
Rename subtitle files and move them parallel to video files for a TV series.

Produces exactly one english SRT subtitle per episode.
"""

from __future__ import annotations

import argparse
from functools import total_ordering
import logging
import os.path
from pathlib import Path
from pprint import pprint as pp
import re
import shutil
import sys


logger = logging.getLogger()


@total_ordering
class EpisodeName:
    KEY_REGEX = re.compile(r'S(\d+)E(\d+)', flags=re.IGNORECASE)
    SUBTITLE_SUFFIX = '.srt'

    def __init__(self, name: str):
        self.name = name

    def get_key(self) -> str|None:
        """
        Extract episode's 'key' from file name.

        The key is an upper-case string in the format 'S00E00', which
        uniquely identifies an episode within a series.

        Args:
            name:
                File name.

        Returns:
            The key if found, otherwise None.
        """
        key = None
        if match := self.KEY_REGEX.search(self.name):
            key = match.group(0)
        return None if key is None else key.upper()

    def get_subtitle_name(self) -> str:
        """
        Build expected subtitle file name for episode.

        Returns:
            Expected file name of subtitle.
        """
        base, suffix = os.path.splitext(self.name)
        subtitle = f"{base}{self.SUBTITLE_SUFFIX}"
        return subtitle

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EpisodeName):
            return NotImplemented
        return self.name == other.name

    def __lt__(self, other: EpisodeName) -> bool:
        if not isinstance(other, EpisodeName):
            return NotImplemented
        return self.name < other.name

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name!r}>"

    def __str__(self) -> str:
        return self.name


class Folder:
    """
    Basic folder operations.
    """
    def __init__(self, folder: Path):
        """
        Initialiser.

        Reads contents of folder into properties.

        Raises:
            RuntimeError:
                If given folder does not appear to contain a TV series.

        Returns:
            None
        """
        if not folder.is_dir():
            raise RuntimeError(f"Path is not a folder: {folder}")
        self.root = folder
        self.folders, self.files = self._read_contents(self.root)

    def _read_contents(
        self,
        root: Path
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """
        Find the files and folders under root.

        Returns:
            Both the folders and files as sorted strings.
        """
        folders = []
        files = []
        for entry in root.iterdir():
            # Skip hidden
            if entry.name.startswith('.'):
                continue

            # Build lists
            if entry.is_dir():
                folders.append(entry.name)
            elif entry.is_file():
                files.append(entry.name)
            else:
                logger.warning(f"Ignoring non-regular file: {entry}")

        folders.sort(key=str.casefold)
        files.sort(key=str.casefold)
        return (tuple(folders), tuple(files))


class SeriesFolder(Folder):
    """
    A folder containing a TV series.

    Expects mulitple video files using the 'S00E00.' naming convention,
    as well as various other subtitle and metadata files.
    """
    SUBTITLE_SUFFIX = '.srt'
    VIDEO_SUFFIXES = ('.mkv', '.mp4', '.webm')

    def __init__(self, folder: Path):
        """
        Initialiser.

        Reads directory contents and runs some basic sanity checks on its contents.

        Raises:
            RuntimeError:
                If given folder does not appear to contain a TV series.
        """
        super().__init__(folder)
        self.episodes = self._find_episodes(self.files)
        self.subtitle_finder = SubtitleFinder(options.folder)

        if len(self.episodes) < 2:
            raise RuntimeError(f"Folder doesn't contain episodes: {folder}")

        logger.info("%s episodes found in %r", len(self.episodes), folder.name)

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return f"<{self.__class__.__name__}: {self.root}>"

    def copy_subtitle(self, episode: EpisodeName, subtitle: Path) -> None:
        """
        Rename and copy subtitle file to the expected location.

        Args:
            episode:
                Episode name.
            subtitle:
                Path to subtitle file.

        Returns:
            Nothing.
        """
        destination = self.root / episode.get_subtitle_name()
        shutil.copy2(subtitle, destination)
        logger.info("Create: %s", destination.name)

    def find_subtitle(self, episode: EpisodeName) -> Path:
        """
        Find a single 'srt' subtitle file for the given episode.
        """
        return self.subtitle_finder.find_subtitle(episode)

    def has_every_subtitle(self) -> bool:
        """
        Does every episode in the folder have a subtitle?

        Returns:
            True only if a properly-named subtitle exists for all episodes.
        """
        have_subtitles = (self.has_subtitle(name) for name in self.episodes)
        return all(have_subtitles)

    def has_subtitle(self, episode: EpisodeName) -> bool:
        return self.subtitle_finder.has_subtitle(episode)

    def _find_episodes(self, files: tuple[str, ...]) -> list[EpisodeName]:
        """
        Find video files that match the 'S00E00' convention.

        Video files are identified just by suffix.

        Args:
            files:
                Tuple of file names.

        Raises:
            RuntimeError:
                If duplicate episode keys are encountered.

        Returns:
            List of episode file names.
        """
        episodes = []
        seen = set()
        for name in self.files:
            _, suffix = os.path.splitext(name)
            if suffix not in self.VIDEO_SUFFIXES:
                continue

            episode = EpisodeName(name)
            key = episode.get_key()
            if key is None:
                continue

            if key in seen:
                message = f"Duplicate episode video files found for {key!r}"
                raise RuntimeError(message)

            episodes.append(episode)
            seen.add(key)

        episodes.sort()
        return episodes


class SubtitleFinder(Folder):
    SUBTITLE_MIN_SIZE = 10_000          # bytes
    SUBTITLE_SUFFIX = '.srt'

    def find_subtitle(self, episode: EpisodeName) -> Path:
        """
        Pick best subtitle for given episode.

        TODO:
            Only handles finding exactly one subtitle file currently.

        Args:
            episode:
                Name of episode file.

        Raises:
            RuntimeError:
                If no subtitle file can be found.

        Returns:
            Paths to subtitle file.
        """
        subtitles = self.list_subtitles(episode)
        if not subtitles:
            raise RuntimeError(f"No subtitle file could be found for {episode!r}")

        # Only one?
        if len(subtitles) == 1:
            return subtitles[0]

        # Narrow by language
        subtitles = self.filter_language(subtitles)

        # Drop files that are too-small
        subtitles = self.filter_small(subtitles)

        # Exactly two? Drop largest
        if len(subtitles) == 2:
            if os.path.getsize(subtitles[0]) > os.path.getsize(subtitles[1]):
                subtitles = [subtitles[1]]
            else:
                subtitles = [subtitles[0]]

        # Only one again?
        if len(subtitles) == 1:
            return subtitles[0]

        raise NotImplementedError(f"Found {len(subtitles)} subtitles for {episode}")

    def filter_language(self, paths: List[Path]) -> List[Path]:
        """
        Filter out non-english subtitles.
        """
        english = []
        for path in paths:
            lowercase = path.name.casefold()
            if 'english' in lowercase or 'eng' in lowercase:
                english.append(path)
        return english

    def filter_small(self, paths: List[Path]) -> List[Path]:
        """
        Drop subtitles that are too small.
        """
        large = []
        for path in paths:
            if os.path.getsize(path) > self.SUBTITLE_MIN_SIZE:
                large.append(path)
        return large

    def has_subtitle(self, episode: EpisodeName) -> bool:
        """
        Does a subtitle file exists for the given episode?

        Args:
            episode:
                File name of episode within folder

        Return:
            True if a matching subtitle exists for the given episode.
        """
        subtitle_path = self.root / episode.get_subtitle_name()
        return True if subtitle_path.is_file() else False

    def list_subtitles(self, episode: EpisodeName) -> list[Path]:
        """
        List all available subtitle files for the given episode.

        Args:
            episode:
                Name of episode file.

        Returns:
            List of paths to subtitle files.
        """
        # In its proper place?
        if self.has_subtitle(episode):
            subtitle = self.root / episode.get_subtitle_name()
            assert subtitle.is_file(), f"Subtitle file not found: {subtitle}"
            return [subtitle]

        # Start looking around
        subtitles: list[Path] = []
        episode_key = episode.get_key()
        assert isinstance(episode_key, str)

        # 'Subs' folder?
        found = self._find_subtitles_in_subs_folder(episode_key)
        subtitles.extend(found)
        return subtitles

    def _find_subtitles_in_subs_folder(self, episode_key: str) -> list[Path]:
        """
        Subtitles found in 'Subs' folder underneath episodes.

        Args:
            episode_key:
                Plain string, eg. 'S02E13'

        RuntimeError:
            Unexpected

        Return:
            Possibly empty list of paths.
        """
        # Abort early if no 'subs' folder found
        subfolder = None
        for name in self.folders:
            if name.casefold() == 'subs':
                subfolder = self.root / name

        if subfolder is None:
            return []

        # Look in subs folder
        subtitles: list[Path] = []

        for entry in subfolder.iterdir():
            entry_key = EpisodeName(entry.name).get_key()
            if entry_key != episode_key:
                continue

            # Match found
            if entry.is_dir():
                subtitles.extend(entry.glob(f"*{self.SUBTITLE_SUFFIX}"))
            elif entry.is_file():
                subtitles.append(entry)
            else:
                raise RuntimeError("Non-regular file found: {entry}")

        logger.debug(
            "%s subtitle(s) found for %s under '%s/%s/'",
            len(subtitles),
            episode_key,
            subfolder.parent.name,
            subfolder.name,
        )
        return subtitles


def argparse_existing_folder(string: str) -> Path:
    """
    An `argparse` type to convert string to a `Path` object.

    Raises `argparse.ArgumentTypeError` if path does not exist.
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


def main(options: argparse.Namespace) -> int:
    folder = SeriesFolder(options.folder)
    if folder.has_every_subtitle():
        print("All subtitles in place, exiting.")
        return 0

    for episode in folder.episodes:
        if folder.has_subtitle(episode):
            continue
        subtitle = folder.find_subtitle(episode)
        folder.copy_subtitle(episode, subtitle)

    return 0


def parse(arguments: list[str]) -> argparse.Namespace:
    description = "Rename and move subtitle files for TV series"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'folder',
        metavar='FOLDER',
        type=argparse_existing_folder,
        help='folder to look under',
    )
    return parser.parse_args()


if __name__ == '__main__':
    logging.basicConfig(format="%(message)s", level=logging.DEBUG)
    options = parse(sys.argv[1:])
    retval = main(options)
    sys.exit(retval)
