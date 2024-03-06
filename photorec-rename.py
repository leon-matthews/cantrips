#!/usr/bin/env python3

"""
Better organise and rename files recovered by ``photorec``.

Copies only good JPEG photos to another directory, using the EXIF date
the photo was taken as the file name.

See: https://www.cgsecurity.org/wiki/PhotoRec
"""

import argparse
import datetime
import logging
from pathlib import Path
import shutil
import sys
from typing import Iterator, Optional

from PIL import UnidentifiedImageError
from PIL.Image import Exif, Image, open as open_image


logger = logging.getLogger(__name__)


def argparse_empty_folder(string: str) -> Path:
    """
    An `argparse` type to check path is an empty folder.

    Raises:
        argparse.ArgumentTypeError:
            If path is not an existing folder.

    Return:
        Path to existing folder.
    """
    path = argparse_existing_folder(string)
    is_empty = not any(path.iterdir())
    if not is_empty:
        message = f"Folder is not empty: {path}"
        raise argparse.ArgumentTypeError(message)
    return path


def argparse_existing_folder(string: str) -> Path:
    """
    An `argparse` type to convert string to a `Path` object.

    Raises:
        argparse.ArgumentTypeError:
            If path is not an existing folder.

    Return:
        Path to existing folder.
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


def build_file_name(image: Image) -> Optional[str]:
    exif = read_exif(image)
    if not exif:
        return None

    photo_taken = parse_datetime(exif)
    if not photo_taken:
        return None

    date = photo_taken.strftime("%Y-%m-%d-%H%M%S")
    return f"{date}.{image.format.lower()}"


def list_files(root: Path) -> Iterator[Path]:
    """
    Recursively list files under root.
    """
    for path in root.glob('**/*'):
        if path.is_file():
            yield path


def read_exif(image: Image) -> Optional[Exif]:
    exif = image.getexif()
    if not exif:
        logger.debug("Could not find Exif metadata in %s", image.filename)
    return exif


def read_image(path: Path) -> Optional[Image]:
    """
    Attempt to open image file.

    Args:
        path:
            Path to image file.

    Return:
        An Image object, or None if input wasn't an image.
    """
    # Attempt to open as image
    try:
        image = open_image(path)
    except UnidentifiedImageError:
        return None
    except OSError as e:
        logger.warning("%s: %s", e, path)
        return None
    else:
        return image


def parse_datetime(exif: Exif) -> Optional[datetime.datetime]:
    """
    Return time photo taken from DateTime string in given exif data.

    Use the Exif.Image.DateTime field, number 306.

    Args:
        string:
            Datetime in exif format 'YYYY:MM:DD HH:MM:SS'

    Returns:
        Datetime, or none if datetime not present.
    """
    string = exif.get(306)
    if string is None:
        return None

    parsed = datetime.datetime.strptime(string, "%Y:%m:%d %H:%M:%S")
    return parsed


def main(options: argparse.Namespace) -> int:
    for path in list_files(options.photorec):
        image = read_image(path)

        # Ignore non-image files
        if not image:
            continue

        # Ignore small images
        if image.height < 1000:
            continue

        # Calculate new name
        file_name = build_file_name(image)
        if not file_name:
            continue

        # Copy to new name
        logger.info("Copy %s to %s", path.name, file_name)
        shutil.copy(path, options.output / file_name)

    return 0


def parse(arguments: list[str]) -> argparse.Namespace:
    description = "Better organise and rename files recovered by 'photorec'"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        metavar='PHOTOREC', dest='photorec', type=argparse_existing_folder,
        help="Folder of files recovered by photorec",
    )
    parser.add_argument(
        metavar='OUTPUT', dest='output', type=argparse_empty_folder,
        help="Folder to output renamed files",
    )
    return parser.parse_args()


if __name__ == '__main__':
    options = parse(sys.argv[1:])
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    return_code = main(options)
    sys.exit(return_code)
