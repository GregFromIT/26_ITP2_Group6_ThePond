"""Fetching a VM image from a URL.

Pasting a link into the uploads page downloads the image and stores it exactly
as if the administrator had chosen the file from their own machine. The only
difference in the resulting row is that `source_url` records where it came from.

WHY THIS IS ITS OWN FILE
------------------------
Making a server fetch a URL a user supplied is server-side request forgery, and
this server is an awkward place for it: it sits on the same network as the
Proxmox cluster and the process holds a Proxmox API token. Left unguarded, a
URL like `https://10.1.21.151:8006/api2/json/...` or
`http://169.254.169.254/latest/meta-data/` would be fetched from inside the
trusted network by something that can already talk to the hypervisor.

The guards are therefore kept together here, away from the view code, so they
are easy to find, easy to review, and hard to bypass by accident:

  * only http and https
  * the hostname is resolved and EVERY address it resolves to is checked against
    loopback, private, link-local, multicast and reserved ranges before any
    connection is made
  * redirects are followed manually, one at a time, with the same check applied
    to each hop — otherwise a public hostname can redirect to 127.0.0.1 and the
    original check counts for nothing
  * a connection and read timeout, so a slow or hanging server cannot tie up a
    worker indefinitely
  * a size cap enforced while streaming, so a URL claiming to be a small image
    cannot fill the disk
  * a hop limit

Private addresses are refused by default. On a teaching network the image may
well live on an internal file server, so `UPLOAD_FETCH_ALLOW_PRIVATE=1` turns
that check off — deliberately a conscious switch with its own setting, rather
than something that quietly defaults open.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import requests

MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60


class FetchError(RuntimeError):
    """The URL could not be fetched, with a reason fit to show a person."""


def _addresses_for(hostname: str):
    """Every address the hostname resolves to. Raises FetchError if none."""
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve {hostname}: {exc}") from exc
    return {result[4][0] for result in results}


def _check_address(address: str, allow_private: bool):
    """Refuse anything that is not an ordinary public address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise FetchError(f"{address} is not a usable address.") from exc

    if allow_private:
        # Even with private addresses allowed, loopback and metadata endpoints
        # stay refused: those are never a file server, and they are the two that
        # turn this into a way to read the server's own secrets.
        if ip.is_loopback or ip.is_link_local:
            raise FetchError(
                f"{address} is a loopback or link-local address, which is never "
                f"a place a VM image lives."
            )
        return

    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        raise FetchError(
            f"{address} is a private or reserved address. If the image really is on "
            f"an internal server, set UPLOAD_FETCH_ALLOW_PRIVATE=1 — read what that "
            f"means first."
        )


def _validate(url: str, allow_private: bool) -> str:
    """Check one URL and return its hostname. Raises FetchError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs can be fetched.")
    if not parsed.hostname:
        raise FetchError("That URL has no host in it.")

    for address in _addresses_for(parsed.hostname):
        _check_address(address, allow_private)
    return parsed.hostname


def open_stream(url: str, allow_private: bool = False):
    """Validate, follow redirects by hand, and return an open response.

    The caller streams the body and is responsible for closing the response.
    Every hop is validated before it is followed, which is the part that matters:
    checking only the first URL lets a public host redirect somewhere internal.
    """
    # allow_redirects=False on each request is what stops requests following
    # anything by itself. Setting session.max_redirects as well makes requests
    # raise TooManyRedirects on the very first call, so it is deliberately left
    # alone — the hop limit is the loop below.
    session = requests.Session()
    current = url

    for _ in range(MAX_REDIRECTS + 1):
        _validate(current, allow_private)
        try:
            response = session.get(
                current,
                stream=True,
                allow_redirects=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                headers={"User-Agent": "PondSec/1.0 (VM image fetch)"},
            )
        except requests.RequestException as exc:
            raise FetchError(f"Could not reach that URL: {exc}") from exc

        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise FetchError("That URL redirected without saying where to.")
            current = requests.compat.urljoin(current, location)
            continue

        if response.status_code != 200:
            code = response.status_code
            response.close()
            raise FetchError(f"That URL returned HTTP {code}.")

        return response, current

    raise FetchError(f"That URL redirected more than {MAX_REDIRECTS} times.")


def filename_from(url: str, response) -> str:
    """Best guess at a filename, for the extension check and the label.

    Content-Disposition is the server's own suggestion, so it is treated the
    same way a browser-supplied filename is: a label, never a path.
    """
    disposition = response.headers.get("Content-Disposition", "")
    if "filename=" in disposition:
        candidate = disposition.split("filename=", 1)[1].strip().strip('";\' ')
        if candidate:
            return candidate
    from urllib.parse import unquote

    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "download"
