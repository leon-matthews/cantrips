#!/usr/bin/env python3
"""
Test a website's sitemap.xml implementation.

Download a website's sitemap.xml and all of the index files that it references,
and report on how many links were found, and what sort of attributes they
contained.

For example::

    $ sitemaps-test.py https://seabreezeapparel.co.nz/
    Downloaded 685B from: https://seabreezeapparel.co.nz/sitemap.xml
        7 URLs found is sitemap index.

    Downloaded 877B from: https://seabreezeapparel.co.nz/sitemap-news.xml
        Found 5 URLs, 5 lastmod, 0 changefreq, and 5 priority fields
    Downloaded 154B from: https://seabreezeapparel.co.nz/sitemap-pages.xml
        Found 0 URLs, 0 lastmod, 0 changefreq, and 0 priority fields
    Downloaded 800B from: https://seabreezeapparel.co.nz/sitemap-inspiration.xml
        Found 4 URLs, 4 lastmod, 0 changefreq, and 4 priority fields
    Downloaded 1.6kB from: https://seabreezeapparel.co.nz/sitemap-brands.xml
        Found 11 URLs, 11 lastmod, 0 changefreq, and 11 priority fields
    Downloaded 2.2kB from: https://seabreezeapparel.co.nz/sitemap-collections.xml
        Found 16 URLs, 16 lastmod, 0 changefreq, and 16 priority fields
    Downloaded 69kB from: https://seabreezeapparel.co.nz/sitemap-products.xml
        Found 495 URLs, 495 lastmod, 0 changefreq, and 495 priority fields
    Downloaded 861B from: https://seabreezeapparel.co.nz/sitemap-static.xml
        Found 6 URLs, 0 lastmod, 6 changefreq, and 6 priority fields

    Total of 537 URLs found in 8 files

"""

import argparse
from dataclasses import dataclass
from enum import Enum
import io
import logging
import math
import sys
from typing import IO, Iterator, List, Optional
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import requests


logger = logging.getLogger(__name__)


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

    Returns: Short string, like '4.3kB'
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


def round_significant(number: float, digits: int = 2) -> float:
    """
    Round number to the given number of sigificant digits. eg::

        >>> round_significant(1235, digits=2)
        1200

    Returns: Number rounded to the given number of digits
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


def xml_strip_iterparse(file_: IO[str]) -> Iterator[ElementTree.Element]:
    """
    Iterate the given file into XML, while stripping namespace prefixes.

    Reading is performed incrementally, using `ElementTree.iterparse()`. This
    has the advantage of being able to start processing as soon as loading
    starts, and not having to hold the document in memory.

    The disadvantage is that elements are not sent out in the order in which
    they appear in the document, but rather as soon as their closing tag is
    encountered.

    Args:
        file_:
            A file-like object.

    Yields: Element
        A single `ElementTree.Element` object as they are closed, ie. not in
        document order.
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

        yield(element)


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
    loc: str
    lastmod: Optional[str]
    changefreq: Optional[str]
    priority: Optional[float]


class SitemapReader:
    """
    Downloads and parses sitemap index and list files.
    """
    def __init__(self, base_url: str):
        """
        Initialise sitemap.xml reader.

        Args:
            base_url (str):
                The URL of the top-level sitemap resource, eg.
                'https://example.com'
        """
        self.session = requests.session()
        self.url_parts = urlsplit(base_url)

    def build_url(self, path: str) -> str:
        """
        Join base URL and path to create full URL.

        Args:
            path:
                Path part of url, eg. '/sitemap.xml'

        Returns:
            Absolute URL
        """
        parts = self.url_parts._replace(path=path)
        return urlunsplit(parts)

    def download_xml(self, url: str) -> Iterator[ElementTree.Element]:
        """
        Fetch a single XML resource.

        Args:
            url:
                Full URL to XML document.

        Raises:
            RuntimeError:
                On any network problems.

        Returns:
            Iterator, as built by `xml_strip_iterparse()`
        """
        try:
            response = self.session.get(url, stream=True, timeout=5.0)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.error(e)
            raise RuntimeError(e) from None
        except requests.exceptions.RequestException as e:
            logger.error(e)
            raise RuntimeError(e) from None
        file_ = io.StringIO(response.text)
        size = file_size(len(response.text))
        logger.info('Downloaded %s from: %s', size, url)
        yield from xml_strip_iterparse(file_)

    def read_index(self) -> List[str]:
        """
        Download root sitemap.xml and find URLs to detail sitemaps.

        <sitemapindex>
            <sitemap>
                <loc>https://example.com/sitemap-news-articles.xml</loc>
            </sitemap>
            ...
        </sitemapindex>

        We should download these other sitemaps and add their 'url' elements
        to the processing queue.

        Returns:
            List of URLs to sitemap files.
        """
        index_url = self.build_url('sitemap.xml')
        data = self.download_xml(index_url)
        urls = []
        for elem in data:
            if elem.tag == 'loc':
                url = elem.text
                assert isinstance(url, str)
                urls.append(url)
        return urls

    def read_sitemap(self, url: str) -> List[Location]:
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
            url:
                Full URL to resource.

        Raises:
            ValueError:
                If any required elements are missing.

        Returns:
            List of `Location` dataclass instances.
        """
        def find_text(elem: ElementTree.Element, tag: str) -> Optional[str]:
            found = elem.find(tag)
            return None if found is None else found.text

        data = self.download_xml(url)
        locations = []
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


def main(options: argparse.Namespace) -> None:
    setup_logging()
    sitemaps = SitemapReader(options.url_base)
    urls = sitemaps.read_index()

    print(f"    {len(urls):,} URLs found is sitemap index.")
    print()

    num_urls_total = 0
    for url in urls:
        locations = sitemaps.read_sitemap(url)

        num_locs = 0
        num_lastmod = 0
        num_changefreq = 0
        num_priority = 0
        for location in locations:
            if location.loc:
                num_locs += 1
            if location.lastmod:
                num_lastmod += 1
            if location.changefreq:
                num_changefreq += 1
            if location.priority:
                num_priority += 1

        num_urls_total += num_locs
        print(
            f"    Found {num_locs:,} URLs, {num_lastmod:,} lastmod, "
            f"{num_changefreq:,} changefreq, and "
            f"{num_priority:,} priority fields"
        )

    print()
    print(f"Total of {num_urls_total:,} URLs found in {len(urls) + 1} files")


def parse(arguments: List[str]) -> argparse.Namespace:
    """
    Parse command-line arguments to produce set of options to give to `main()`.
    """
    description = "Test sitemap.xml files and their links"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'url_base',
        default='http://localhost:8000',
        metavar='BASE_URL',
        nargs='?',
        help='Base url of site, eg. http://localhost:8000')
    return parser.parse_args()


def setup_logging() -> None:
    logging.basicConfig(
        format='%(message)s',
        level=logging.INFO,
    )


if __name__ == '__main__':
    error = 0
    options = parse(sys.argv[1:])
    try:
        main(options)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        error = 1
    sys.exit(error)
