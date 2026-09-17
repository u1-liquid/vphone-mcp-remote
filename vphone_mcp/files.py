"""Hand tool-produced files to remote clients over the HTTP transport.

Tool results name paths on the *server* host — the screenshot PNG, a launch
log. That is all a local stdio client needs, but a remote agent cannot open any
of them. This module turns such a path into a fetchable URL::

    http://<hash>:<mcp-token>@host:8765/files/screenshots/screen.png
           └ sha256 of the resolved absolute path
                   └ the same bearer token the MCP endpoint uses

Exactly one directory is served: ``$VPHONE_ROOT/vphone-mcp``, which is this
server's own — ``logs/`` and ``screenshots/``. The VM library and the rest of
the vphone root stay unreachable; anything worth fetching is written here.

The URL exposes a path relative to that root, and carries the hash of the full
path it was minted for as the Basic-auth *user*, with the MCP token as the
*password*. Serving therefore needs no state at all: join the relative path onto
the root, normalise it, and serve only when the result stays inside the root, is
the spelling this module would have minted for it, and hashes to what the
request presented. A tampered path — ``../``, an absolute path, a different
file — resolves elsewhere, so its hash no longer matches and it never reaches
the filesystem. Paths are resolved before the scope check, so a symlink inside
the root pointing outside it is rejected too.
"""

import hashlib
import hmac
import os
from pathlib import Path
from urllib.parse import quote

ROUTE_PREFIX = "/files"

_base_url = ""  # scheme://host[:port], set for network transports
_token = ""  # MCP bearer token, used as the Basic-auth password


# ---------------------------------------------------------------------------
# The served root
# ---------------------------------------------------------------------------

def vphone_root() -> Path:
    return _resolved(os.environ.get("VPHONE_ROOT") or Path.home() / ".vphone")


def root() -> Path:
    """The only directory whose files may be served."""
    return vphone_root() / "vphone-mcp"


def screenshot_dir() -> Path:
    """Default destination for ``screenshot()`` — inside the root, so the
    capture is fetchable remotely without the agent having to pick a path."""
    return root() / "screenshots"


def log_dir() -> Path:
    """Where detached ``vm launch`` / ``boot`` processes write their logs."""
    return root() / "logs"


def _resolved(path: str | Path) -> Path:
    """Absolute path with symlinks followed — the identity used everywhere."""
    return Path(path).expanduser().resolve()


def in_scope(path: str | Path) -> bool:
    """True when path resolves inside :func:`root`."""
    try:
        target = _resolved(path)
    except (OSError, RuntimeError):
        return False
    base = root()
    return target == base or target.is_relative_to(base)


def scope_error(path: str | Path) -> str:
    """Message explaining why path is not servable."""
    return (
        f"{path} is outside {root()}, the only directory the file endpoint "
        "serves. Write it there (or under logs/ or screenshots/) if the client "
        "has to fetch it."
    )


# ---------------------------------------------------------------------------
# Hash / lookup
# ---------------------------------------------------------------------------

def hash_for(path: str | Path) -> str:
    """SHA-256 of the resolved absolute path — the Basic-auth user."""
    return hashlib.sha256(str(_resolved(path)).encode()).hexdigest()


def relative_path(path: str | Path) -> str | None:
    """Path relative to the root, or None when it is out of scope."""
    if not in_scope(path):
        return None
    return _resolved(path).relative_to(root()).as_posix()


def resolve(relative: str, presented_hash: str) -> Path | None:
    """The file a request is for, or None.

    Served only when the relative path, joined onto the root and normalised,
    stays inside it, is the canonical spelling of the result (the one
    :func:`relative_path` would mint, so each file has exactly one URL), hashes
    to what the request presented, and is a regular file.
    """
    if not presented_hash or "\x00" in relative:
        return None
    # An absolute path would swallow the root in the join below.
    relative = relative.lstrip("/")
    try:
        candidate = _resolved(root() / relative)
    except (OSError, RuntimeError):
        return None
    if relative_path(candidate) != relative:
        return None
    if not hmac.compare_digest(hash_for(candidate), presented_hash):
        return None
    if not candidate.is_file():
        return None
    return candidate


# ---------------------------------------------------------------------------
# URL emission
# ---------------------------------------------------------------------------

def configure(base_url: str, token: str) -> None:
    """Enable URL emission against a base like ``https://host:8765``."""
    global _base_url, _token
    _base_url = base_url.rstrip("/")
    _token = token


def enabled() -> bool:
    return bool(_base_url)


def url_for(path: str | Path) -> str | None:
    """Return the URL for a servable path, or None when it is not servable."""
    if not _base_url:
        return None
    relative = relative_path(path)
    if relative is None:
        return None
    scheme, sep, host = _base_url.partition("://")
    userinfo = f"{hash_for(path)}:{quote(_token, safe='')}@"
    segments = "/".join(quote(part, safe="") for part in relative.split("/"))
    return f"{scheme}{sep}{userinfo}{host}{ROUTE_PREFIX}/{segments}"
