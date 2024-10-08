#!/usr/bin/env python3
"""
Test a website's sitemap.xml implementation.

Download a website's sitemap.xml and all of the index files that it references,
and report on how many links were found, and what sort of attributes they
contained.

For example::

    $ sitemaps-test.py lost.co.nz
    Downloaded 327B from: https://lost.co.nz/sitemap.xml
        Sitemap index found containing 3 URLs

    Downloaded 1.6kB from: https://lost.co.nz/sitemap-links.xml
        Found 12 URLs, 12 lastmod, 12 changefreq, and 12 priority fields
    Downloaded 2.3kB from: https://lost.co.nz/sitemap-articles.xml
        Found 17 URLs, 17 lastmod, 17 changefreq, and 17 priority fields
    Downloaded 1.2kB from: https://lost.co.nz/sitemap-projects.xml
        Found 9 URLs, 9 lastmod, 9 changefreq, and 9 priority fields

    Total of 39 URLs found in 4 files


Requires:
    Python 3.9+ and the 3rd-party `requests` library for HTTP downloads.

"""

import argparse
import collections
from dataclasses import dataclass
from enum import Enum
import functools
import io
import logging
import math
import sys
from typing import IO, Iterator, List, Optional, TypeAlias
from urllib.parse import SplitResult, urlsplit, urlunsplit
from xml.etree import ElementTree

import requests


logger = logging.getLogger(__name__)


XML: TypeAlias = Iterator[ElementTree.Element]


def file_size(size: int, traditional: bool = False) -> str:
    """
    Convert a file size in bytes to a easily human-parseable form, using only
    one or two significant figures.

    Raises:
        ValueError: If given size has an error in its... ah... value.

    Args:
        size:
            file size in bytes
        traditional:
            Use traditional base-2 units, otherwise default to using
            'proper' SI multiples of 1000.

    Returns:
        Short string, like '4.3kB'
    """
    try:
        size = int(size)
    except (ValueError, TypeError):
        raise ValueError("Given file size '{}' not numeric.".format(size))

    if size < 0:
        raise ValueError("Given file size '{}' not positive".format(size))

    if size < 1000:
        return '{}B'.format(size)

    suffixes = {
        1000: ['kB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'],
        1024: ['KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']
    }

    multiple = 1024 if traditional else 1000
    divided = float(size)
    for suffix in suffixes[multiple]:
        divided /= multiple
        if divided < multiple:
            divided = round_significant(divided, 2)
            divided = int(divided) if divided >= 10 else divided
            return '{:,}{}'.format(divided, suffix)

    # Greater than 1000 Yottabytes!? That is a pile of 64GB MicroSD cards
    # as large as the Great Pyramid of Giza!  You're dreaming, but in the
    # interests of completeness...
    # http://en.wikipedia.org/wiki/Yottabyte
    return '{:,}{}'.format(int(round(divided)), suffix)


def find_text(elem: ElementTree.Element, tag: str) -> Optional[str]:
    """
    Simple helper to find text inside tag inside element or return none.
    """
    found = elem.find(tag)
    return None if found is None else found.text


def round_significant(number: float, digits: int = 2) -> float:
    """
    Round number to the given number of sigificant digits. eg::

        >>> round_significant(1235, digits=2)
        1200

    Returns:
        Number rounded to the given number of digits
    """
    digits = int(digits)
    if digits <= 0:
        raise ValueError("Must have more than zero significant digits")

    if not number:
        return 0
    number = float(number)
    magnitude = int(math.floor(math.log10(abs(number))))
    ndigits = digits - magnitude - 1
    return round(number, ndigits)


def xml_strip_iterparse(file_: IO[str]) -> XML:
    """
    Iterate the given file into XML, while stripping namespace prefixes.

    Python's `xml.etree` parser is performant and its API is easy to work
    *except* when the document has a namespace, in which case that namespace
    is forced into every lookup, eg.

        >>> elem.find('{http://www.sitemaps.org/schemas/sitemap/0.9}urlset')

    This function manipulates the tree so that that is no longer required.

        >>> elem.find('urlset')

    There has been discussion about addressing this problem in core Python,
    but at the time of writing, that has yet to be introduced into the stdlib.

    Args:
        file_:
            A file-like object.

    Returns:
        A generator over input file, to allow for large inputs, yielding
        `ElementTree.Element` objects
    """
    for event, element in ElementTree.iterparse(file_):
        # Replace 'tag' string
        tag = element.tag
        if tag and isinstance(tag, str) and tag[0] == '{':
            element.tag = tag.partition('}')[2]

        # Modify 'attrib' dictionary in-place
        attrib = element.attrib
        if attrib:
            for name, value in list(attrib.items()):
                if name and isinstance(name, str) and name[0] == '{':
                    del attrib[name]
                    attrib[name.partition('}')[2]] = value
        yield element


class ChangeFreq(Enum):
    ALWAYS = 'always'
    HOURLY = 'hourly'
    DAILY = 'daily'
    WEEKLY = 'weekly'
    MONTHLY = 'monthly'
    YEARLY = 'yearly'
    NEVER = 'never'


@dataclass
class Location:
    """
    Collect the core fields for sitemap elements that describe an end-point.

    Used by `SitemapReader.read_sitemap()`
    """
    loc: str
    lastmod: Optional[str]
    changefreq: Optional[str]
    priority: Optional[float]


class Downloader:
    def __init__(self, base_url: str):
        """
        Initialiser.

        Args:
            base_url (str):
                The URL of the top-level sitemap resource, eg.
                'https://example.com'
        """
        self.base_url = self._clean_url(base_url)

        # Allow reuse of TCP connection
        self.session = requests.session()

    def build_url(self, path: str) -> str:
        """
        Join base URL and path to create full URL.

        Args:
            path:
                Path part of url, eg. '/sitemap.xml'

        Returns:
            Absolute URL

        """
        parts = urlsplit(self.base_url)
        if not parts.scheme:
            parts._replace(scheme='https')
        parts = parts._replace(path=path)
        url = urlunsplit(parts)
        return url

    @functools.lru_cache(maxsize=1)
    def get_text(self, url: str) -> str:
        """
        Fetch a single resource and convert to unicode string.

        Args:
            url:
                Absolute URL to resource, eg. 'https://example.com/robots.txt'

        Raises:
            RuntimeError:
                On any network problems.

        Returns:
            Plain unicode string.
        """
        try:
            response = self.session.get(url, stream=True, timeout=5.0)
            response.raise_for_status()
            text = response.text
        except requests.exceptions.HTTPError as e:
            logger.error(e)
            raise RuntimeError(e) from None
        except requests.exceptions.RequestException as e:
            logger.error(e)
            raise RuntimeError(e) from None

        logger.info('Downloaded %s from: %s', file_size(len(text)), url)
        return text

    def get_xml(self, url: str) -> XML:
        """
        Fetch and pre-parse XML resource

        Args:
            url:
               Full URL to resource, eg. 'https://example.com/sitemap.xml'

        Returns:
            Iterator, as built by `xml_strip_iterparse()`
        """
        # Build file-like object from string for `xml.etree`
        text = self.get_text(url)
        file_ = io.StringIO(text)
        yield from xml_strip_iterparse(file_)

    def _clean_url(self, base_url: str) -> str:
        """
        Add schema to base_url, if missing.
        """
        parts = urlsplit(base_url)
        if not parts.scheme:
            parts = SplitResult('https', parts.path, '', '', '')
        return urlunsplit(parts)


class SitemapReader:
    """
    Parse sitemap index and list files.
    """
    def __init__(self, domain: str):
        """
        Initialiser.

        Args:
            domain:
                The hostname of website to check, eg. example.com
        """
        self.downloader = Downloader(domain)

    def print_index(self, urls: list[str]) -> None:
        """
        Print details about the sitemap index, and the sitemaps pointed to.
        """
        total_urls = 1                  # We started with '/sitemap.xml'
        print(f"    Sitemap index found containing {len(urls):,} URLs")
        print()

        for url in urls:
            locations = self.read_sitemap(url)
            self.print_sitemap(locations)
            total_urls += len(locations)

        print()
        print(f"Total of {total_urls:,} URLs found in {len(urls) + 1} files")

    def print_sitemap(self, locations: List[Location]) -> None:
        """
        Print basic details about sitemap file.
        """
        fields = ('loc', 'lastmod', 'changefreq', 'priority')

        # Build count of implemented fields
        counts: dict[str, int] = collections.Counter()

        for location in locations:
            for field in fields:
                if hasattr(location, field):
                    counts[field] += 1

        print(
            f"    Found {counts['loc']:,} URLs, {counts['lastmod']:,} lastmod, "
            f"{counts['changefreq']:,} changefreq, and "
            f"{counts['priority']:,} priority fields"
        )

    def read_index(self, path: str) -> tuple[list[str], list[Location]]:
        """
        Extract URLs to detail sitemaps, if any, from sitemap index.

        <sitemapindex>
            <sitemap>
            <loc>https://example.com/sitemap-news-articles.xml</loc>
            </sitemap>
            ...
        </sitemapindex>

        We should download these other sitemaps and add their 'url' elements
        to the processing queue.

        Args:
            path:
                Path to file, eg '/sitemap.xml'

        Returns:
            List of URLs to sitemap files.
        """

        url = self.downloader.build_url(path)
        data = self.downloader.get_xml(url)

        urls = []
        for elem in data:
            if elem.tag == 'sitemap':
                loc = find_text(elem, 'loc')
                if loc is None:
                    raise ValueError('Missing required <loc> element')
                else:
                    urls.append(loc)

        locations = self.read_sitemap(url)

        return (urls, locations)

    def read_sitemap(self, path: str) -> List[Location]:
        """
        Download sitemap file, build list of `Location` data objects.

        <urlset>
            <url>
                <loc>https://example.com/articles/question-empathy/</loc>
                <changefreq>daily</changefreq>
                <lastmod>2019-03-25</lastmod>
                <priority>0.9</priority>
            </url>
            ...
        </urlset>

        Args:
            path:
                Path to sitemap file, eg, "/sitemap-products.xml"

        Raises:
            ValueError:
                If any required elements are missing.

        Returns:
            List of `Location` dataclass instances.
        """
        locations = []
        data = self.downloader.get_xml(path)
        for elem in data:
            if elem.tag == 'url':
                priority = (
                    float(temp)
                    if (temp := find_text(elem, 'priority'))
                    else None
                )
                loc = find_text(elem, 'loc')
                if loc is None:
                    raise ValueError('Missing required <loc> element')

                location = Location(
                    loc=loc,
                    lastmod=find_text(elem, 'lastmod'),
                    changefreq=find_text(elem, 'changefreq'),
                    priority=priority,
                )
                locations.append(location)
        return locations


def parse(arguments: List[str]) -> argparse.Namespace:
    """
    Parse command-line arguments to produce set of options to give to `main()`.
    """
    description = "Test sitemap.xml files and their links"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'url_base',
        metavar='BASE_URL',
        help='Base url of site, eg. https://example.com/')
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        format='%(message)s',
        level=logging.INFO,
    )


def main(options: argparse.Namespace) -> None:
    """
    Script entry point.
    """
    setup_logging()
    sitemaps = SitemapReader(options.url_base)
    urls, locations = sitemaps.read_index('/sitemap.xml')

    # Sitemap index or plain file?
    if urls:
        sitemaps.print_index(urls)
    else:
        sitemaps.print_sitemap(locations)


if __name__ == '__main__':
    exit_code = 0
    options = parse(sys.argv[1:])
    try:
        main(options)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        exit_code = 1
    sys.exit(exit_code)
