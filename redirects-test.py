#!/usr/bin/env python3
"""
Verify that your DNS and HTTP redirects are working correctly.

$ redirects-test.py https://lost.co.nz/ http://lost.co.nz http://www.lost.co.nz/
Checking 3 URLs
https://lost.co.nz/    -> 200
http://lost.co.nz/     -> 301 -> https://lost.co.nz/ -> 200
http://www.lost.co.nz/ -> 301 -> https://lost.co.nz/ -> 200
"""

import argparse
from collections import defaultdict
from pprint import pprint as pp
from typing import List, Optional, Tuple
import socket
import sys
from urllib.parse import urlsplit, urlunsplit

import requests


def add_hostname_prefix(url: str, prefix: str):
    """
    Build new absolute URL with the hostname prefixed.

    Args:
        url:
            Absolute URL
        prefix:
            Prefix to add. Must end with full-stop, ie. 'www.'

    Raises:
        ValueError:
            If hostname already starts with the given prefix

    Returns:
        Absolute URL.
    """
    assert '//' in url, f"Expected absolute URL, given: {url!r}"
    parts = urlsplit(url)
    if parts.netloc.startswith(prefix):
        raise ValueError(f'Hostname already has prefix {prefix!r}: {parts.netloc}')
    hostname = f"{prefix}{parts.netloc}"
    return urlunsplit((parts.scheme, hostname, parts.path, '', ''))


def clean_url(
    url: str,
    default_scheme: str = 'http',
    default_path: str = '/',
    prefixes: Optional[List[str]] = None
) -> List[str]:
    """
    Build URLs to test from given base.

    Args:
        url:
            Anything from a bare domain name to an absolute URL.
        default_scheme:
            Use this scheme if given URL does not have one.
        default_path:
            Use this path if given URL does not have one.
        prefixes:
            Additional prefixes to add to URL, eg. 'www'. One extra URL
            will be returned per prefix given.

    Returns:
        A list of URLs to test
    """
    # Build first URL
    if '//' not in url:
        url = f"{default_scheme}://{url}"
    parts = urlsplit(url)
    path = parts.path if parts.path else default_path
    url = urlunsplit((parts.scheme, parts.netloc, path, '', ''))
    return url


def dns_lookup(url: str) -> Tuple[str, str]:
    """
    Attempt to resolve single hostname to a IPv4 address.

    Args:
        url (str):
            Absoulute URL to lookup DNS for.

    Returns:
        2-tuple of (hostname, address).
        Address is either a string, or None if lookup failed.
    """
    assert '//' in url, f"Expected absolute URL, given: {url!r}"
    parts = urlsplit(url)
    hostname = parts.netloc

    try:
        address = socket.gethostbyname(hostname)
    except OSError as e:
        address = None
    return (hostname, address)


class RedirectChecker:
    def __init__(self, url):
        self.error = None
        self.url = url
        self.response = self.get()

    def get(self) -> Optional[requests.Response]:
        """
        Perform GET, catch exceptions.

        Returns:
            Response if possible, None on error.
        """
        try:
            response = requests.get(self.url, allow_redirects=True, timeout=5.0)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            self.error = 'TIMEOUT'
        except requests.exceptions.TooManyRedirects:
            self.error = 'REDIRECT LOOP'
        except requests.exceptions.ConnectionError as e:
            hostname, ip = dns_lookup(self.url)
            if ip is None:
                self.error = 'DNS ERROR'
            else:
                self.error = 'CONNECTION ERROR'
        except requests.exceptions.HttpError:
            self.error = 'HTTP ERROR'
        except requests.exceptions.RequestException:
            self.error = f"ERROR: {e}"

    def get_history(self) -> List[Tuple[str, str]]:
        """
        Return history as list of (URL, code) tuples.

        Code is either the HTTP response code or a short error string.
        """
        if self.error:
            return [(self.url, self.error)]

        parts = []
        for history in self.response.history:
            parts.append((history.url, history.status_code))
        parts.append((self.response.url, self.response.status_code))
        return parts

    def __str__(self):
        """
        Build nice string representation of history.
        """
        if self.error:
            return f"{self.url} -> {self.error}"

        parts = []
        for url, code in self.get_history():
            parts.append(str(url))
            parts.append(str(code))
        return ' -> '.join(parts)


def print_checkers(
    checkers: List[RedirectChecker],
    prefixes: Optional[List[str]] = None
) -> None:
    """
    Print checks as a fixed-width format.
    """
    if prefixes is None:
        prefixes = []

    # Extract and sort history tuples
    histories = []
    for checker in checkers:
        histories.append(checker.get_history())

    def key(history):
        url = history[0][0]
        parts = urlsplit(url)
        domain = parts.netloc
        base = domain
        for prefix in prefixes:
            if base.startswith(prefix):
                base = base.removeprefix(prefix)
                break
        return base, domain

    histories = sorted(histories, key=key)

    # Calculate with of each column
    longest = defaultdict(int)
    for history in histories:
        for index, (url, code) in enumerate(history):
            longest[index] = max(len(url), longest[index])

    # Print columns
    for history in histories:
        parts = []
        for index, (url, code) in enumerate(history):
            longest_url = longest[index]
            parts.append(f"{url:<{longest_url}}")
            parts.append(f"{code}")
        print(' -> '.join(parts))


class Main:
    def __init__(self, args):
        options = self.parse(args)
        self.prefixes = self.build_prefix_list(options)
        self.url_list = self.build_url_list(options)

    def __call__(self):
        num_urls = len(self.url_list)
        plural = 'URL' if num_urls == 1 else 'URLs'
        print(f"Checking {num_urls:,} {plural}")

        # Check URLs
        checkers = []
        for url in self.url_list:
            checker = RedirectChecker(url)
            checkers.append(checker)

        # Print output
        print_checkers(checkers, self.prefixes)

    def build_prefix_list(self, options):
        prefixes = [] if options.prefix is None else options.prefix
        return [f"{prefix}." for prefix in prefixes]

    def build_url_list(self, options):
        url_list = []

        # Read URLs from args or file, clean.
        default_scheme = 'https' if options.https else 'http'
        if options.urls:
            url_list = self.url_list_clean(options.urls, default_scheme)
        elif options.urls_from:
            url_list = self.url_list_read(options.urls_from)
            url_list = self.url_list_clean(url_list, default_scheme)
        else:
            raise RuntimeError("No URLs provided to check")

        # Add prefixes
        prefixed = []
        for url in url_list:
            for prefix in self.prefixes:
                try:
                    prefixed.append(add_hostname_prefix(url, prefix))
                except ValueError:
                    pass
        url_list.extend(prefixed)

        return url_list

    def url_list_clean(
        self,
        dirty_urls: List[str],
        default_scheme: str,
    ) -> List[str]:
        """
        Build clean list of URLs to test.
        """
        urls = []
        default_path = '/'
        for url in dirty_urls:
            url = clean_url(url, default_scheme, default_path)
            urls.append(url)
        return urls

    def url_list_read(self, textio) -> List[str]:
        """
        Read lines form text file, skipping blank lines. and comments.
        """
        lines = [
            stripped for line in textio
            if (stripped := line.strip()) and not stripped.startswith('#')
        ]
        textio.close()
        return lines

    def parse(self, arguments: List[str]) -> argparse.Namespace:
        """
        Parse command-line arguments.
        """
        description = "Test HTTP redirects"
        parser = argparse.ArgumentParser(
            allow_abbrev=False,
            description=description,
        )

        # Prefixes?
        parser.add_argument(
            '--prefix', action='append',
            help="add extra prefixes to domain")

        # HTTP or HTTPS?
        parser.add_argument(
            '--https', action='store_true',
            help='use HTTPS if no schema given (default is HTTP)',
            )

        # URLs
        url_group = parser.add_mutually_exclusive_group(required=True)

        url_group.add_argument(
            '--urls-from',
            metavar='PATH',
            type=argparse.FileType('r'),
            help='read URL list from file'
        )

        # URLs
        url_group.add_argument(
            'urls', default='', metavar='URL', nargs='*',
            help="URL to check",
        )
        return parser.parse_args()


if __name__ == '__main__':
    error = 0
    main = Main(sys.argv[1:])
    try:
        main()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        error = 1
    sys.exit(error)
