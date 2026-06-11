"""Extract and persist an authenticated Trendlyne session.

Trendlyne runs on Django, so authentication is a two-step flow:

1. ``GET`` the login page to obtain a ``csrftoken`` cookie and the matching
   hidden ``csrfmiddlewaretoken`` form field.
2. ``POST`` the credentials together with the CSRF token. On success the server
   responds with a ``sessionid`` cookie that authenticates subsequent requests.

This module logs in, pulls the resulting session cookies out of the session
jar, and writes them to disk so they can be reused without logging in again.

Credentials are read from the environment (never hard-code them)::

    export TRENDLYNE_EMAIL="you@example.com"
    export TRENDLYNE_PASSWORD="..."
    python trendlyne_session.py

The saved file (``trendlyne_session.json`` by default) contains the cookies and
the CSRF token. Treat it like a password: anyone holding it can act as you on
Trendlyne.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import requests

BASE_URL = "https://trendlyne.com"
LOGIN_PATH = "/login/"
# A page that requires authentication; used to verify the session is live.
VERIFY_PATH = "/portfolio/"

# Trendlyne rejects requests that don't look like a real browser.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class TrendlyneAuthError(RuntimeError):
    """Raised when login fails or a session cannot be established."""


@dataclass
class TrendlyneSession:
    """The pieces needed to replay an authenticated Trendlyne session."""

    session_id: str
    csrf_token: str
    cookies: dict
    created_at: float

    def to_cookie_header(self) -> str:
        """Render the cookies as a single ``Cookie`` header value."""
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())

    def save(self, path: Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(asdict(self), indent=2))
        # Session files are secrets; keep them owner-readable only.
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path) -> "TrendlyneSession":
        data = json.loads(Path(path).read_text())
        return cls(**data)


def _extract_csrf_token(session: requests.Session, html: str) -> Optional[str]:
    """Return the CSRF token from the cookie jar, falling back to the form."""
    token = session.cookies.get("csrftoken")
    if token:
        return token

    # Fall back to scraping the hidden form field if the cookie is absent.
    import re

    match = re.search(
        r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', html
    )
    return match.group(1) if match else None


def extract_session(
    email: str,
    password: str,
    *,
    timeout: float = 30.0,
    verify: bool = True,
) -> TrendlyneSession:
    """Log in to Trendlyne and return the authenticated session.

    Args:
        email: Account email / username.
        password: Account password.
        timeout: Per-request timeout in seconds.
        verify: If True, hit an authenticated page to confirm the session works.

    Raises:
        TrendlyneAuthError: If any step of the login flow fails.
    """
    if not email or not password:
        raise TrendlyneAuthError("Email and password are both required.")

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    # Step 1: prime the CSRF cookie.
    login_url = BASE_URL + LOGIN_PATH
    try:
        resp = session.get(login_url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TrendlyneAuthError(f"Could not load the login page: {exc}") from exc

    csrf_token = _extract_csrf_token(session, resp.text)
    if not csrf_token:
        raise TrendlyneAuthError(
            "No CSRF token found on the login page; the login flow may have changed."
        )

    # Step 2: post credentials with the CSRF token. Django expects the token in
    # both the form body and the Referer header.
    payload = {
        "csrfmiddlewaretoken": csrf_token,
        "email": email,
        "username": email,
        "password": password,
    }
    try:
        resp = session.post(
            login_url,
            data=payload,
            headers={"Referer": login_url},
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TrendlyneAuthError(f"Login request failed: {exc}") from exc

    session_id = session.cookies.get("sessionid")
    if not session_id:
        raise TrendlyneAuthError(
            "Login did not return a sessionid cookie — check your credentials."
        )

    # The CSRF token is often rotated after login; capture the current one.
    csrf_token = session.cookies.get("csrftoken", csrf_token)

    result = TrendlyneSession(
        session_id=session_id,
        csrf_token=csrf_token,
        cookies=session.cookies.get_dict(),
        created_at=time.time(),
    )

    if verify and not _is_authenticated(session, timeout=timeout):
        raise TrendlyneAuthError(
            "Session cookie obtained but the account does not appear authenticated."
        )

    return result


def _is_authenticated(session: requests.Session, *, timeout: float) -> bool:
    """Probe an authenticated page; True if we are not bounced to login."""
    try:
        resp = session.get(
            BASE_URL + VERIFY_PATH, timeout=timeout, allow_redirects=False
        )
    except requests.RequestException:
        return False
    # A redirect to /login/ means the session is not valid.
    location = resp.headers.get("Location", "")
    if resp.status_code in (301, 302) and "login" in location.lower():
        return False
    return resp.status_code == 200


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract an authenticated Trendlyne session and save it to disk."
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("TRENDLYNE_EMAIL"),
        help="Trendlyne account email (default: $TRENDLYNE_EMAIL).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("TRENDLYNE_PASSWORD"),
        help="Trendlyne account password (default: $TRENDLYNE_PASSWORD).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("trendlyne_session.json"),
        help="Where to write the session file (default: ./trendlyne_session.json).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-login authenticated-page check.",
    )
    parser.add_argument(
        "--print-cookie",
        action="store_true",
        help="Print the Cookie header to stdout instead of writing a file.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not args.email or not args.password:
        print(
            "error: provide credentials via --email/--password or the "
            "TRENDLYNE_EMAIL / TRENDLYNE_PASSWORD environment variables.",
            file=sys.stderr,
        )
        return 2

    try:
        session = extract_session(
            args.email, args.password, verify=not args.no_verify
        )
    except TrendlyneAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.print_cookie:
        print(session.to_cookie_header())
    else:
        session.save(args.out)
        print(f"Session saved to {args.out} (sessionid + csrftoken captured).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
