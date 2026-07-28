"""Credential redaction for connection URLs.

Used everywhere a cache URL reaches a log line or an exception message. It is a
diagnostic helper, so it is written never to raise and never to fall back to
returning the URL it failed to redact.
"""

import urllib.parse


def hide_url_password(url: str) -> str:
    """Masks password in URL for safe logging.

    Replaces actual password with '***' while preserving URL structure.

    The host portion of the netloc is carried over verbatim rather than
    reassembled from ``parsed.hostname`` and ``parsed.port``: ``hostname`` drops
    the brackets an IPv6 literal needs, and ``port`` raises ``ValueError`` on a
    non-numeric port — which would replace a caller's own "invalid URL" message
    with a confusing one from inside :mod:`urllib`.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        # Malformed beyond parsing. Echoing it back could publish the very
        # password this function exists to hide, so say nothing instead.
        return "<unparsable url>"

    # Split on the *last* '@': a password may legally contain one.
    userinfo, separator, host = parsed.netloc.rpartition("@")
    if not separator or ":" not in userinfo:
        # No credentials, or a username with no password — nothing to mask.
        return url

    # if only a password (no username), userinfo is ":pw" → user == "" → ":***@host"
    user = userinfo.partition(":")[0]

    safe_parsed = urllib.parse.ParseResult(
        scheme=parsed.scheme,
        netloc=f"{user}:***@{host}",
        path=parsed.path,
        params=parsed.params,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return safe_parsed.geturl()
