#!/usr/bin/env python3

"""
Download and extract 'GeoLite2-Country.mmdb' database into current folder.
"""

import argparse
from pathlib import Path
import shutil
import sys
import tarfile
from tempfile import TemporaryDirectory
import time
import os

import requests


ENV_KEY = 'MAXMIND_LICENSE_KEY'
OUTPUT = 'GeoLite2-Country.mmdb'
SUFFIX = 'tar.gz'
URL_TEMPLATE = (
    "https://download.maxmind.com/app/geoip_download?"
    "edition_id=GeoLite2-Country&license_key={license_key}&suffix={suffix}"
)


def download(url: str, destination: Path) -> Path:
    """
    Download binary file at the given URL.

    Args:
        url:
            Full URL to file.
        destination:
            Path of file to write to.

    Raises:
        FileExistsError:
            If destination already exists.
        requests.RequestException:
            This or various subclasses when download fails.

    Returns:
        Path to successfully downloaded file.
    """
    if destination.exists():
        raise FileExistsError(f"File already exists: {destination}")

    start = time.perf_counter()
    response = requests.get(url, stream=True)
    response.raise_for_status()
    size = 0
    with open(destination, 'wb') as f:
        for chunk in response.iter_content(chunk_size=None):
            f.write(chunk)
            size += len(chunk)
    elapsed = time.perf_counter() - start
    print(f"Downloaded {destination.name} ({size:,} bytes) in {elapsed:.2f} seconds")
    return destination


def extract_tar(path: Path, folder: Path) -> int:
    """
    Extract tar file into given folder.

    Args:
        path:
            Path to tar file. May be compressed.
        folder:
            Folder in which to extract tar file's contents.

    Returns:
        Number of bytes extracted.
    """
    if not (folder.exists() and folder.is_dir()):
        raise FileNotFoundError(f"Output folder not found: {folder}")

    num_files = num_dirs = num_bytes = 0
    with tarfile.open(path, 'r') as tar:
        for m in tar.getmembers():
            if m.isdir():
                num_dirs += 1
            if m.isfile():
                num_files += 1
            num_bytes += m.size
        tar.extractall(path=folder, filter='data')
    print(
        f"Extracted {num_dirs:,} folder(s) and {num_files:,} files, "
        f"totalling {num_bytes:,} bytes"
    )
    return num_bytes


def find_file(name: str, folder: Path) -> Path:
    """
    Return the path of the first file with `name` found under `root`.

    Args:
        name:
            Name of file to find. Case-sensitive.
        folder:
            Folder to start looking under.

    Raises:
        FileNotFoundError:
            If no file found, or if root no a folder.

    Returns:
        Path of file found.
    """
    for root, dirs, files in os.walk(folder):
        for f in files:
            if name == f:
                return Path(root) / f

    raise FileNotFoundError(f"Could not find file named: {name!r}")


def parse(args: list[str]) -> argparse.Namespace:
    description = "Download GeoLite2 database into current folder"
    parser = argparse.ArgumentParser(description=description)
    return parser.parse_args(args)


def main(options: argparse.Namespace) -> None:
    license_key = os.environ.get(ENV_KEY)
    if not license_key:
        sys.exit(f"Environment variable {ENV_KEY} is not set")
    url = URL_TEMPLATE.format(license_key=license_key, suffix=SUFFIX)
    filename = f"{OUTPUT}.{SUFFIX}"
    with TemporaryDirectory(prefix='geolite2_download_') as folder:
        folder = Path(folder)
        path = download(url, folder / Path(filename))
        extract_tar(path, folder)
        database = find_file(OUTPUT, folder)
        shutil.copyfile(database, Path('.') / OUTPUT)


if __name__ == '__main__':
    code = 0
    options = parse(sys.argv[1:])
    try:
        main(options)
    except:
        code = 1
        raise

    sys.exit(code)
