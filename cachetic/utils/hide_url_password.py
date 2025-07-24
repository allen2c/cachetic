import urllib.parse


def hide_url_password(url: str) -> str:
    """Masks password in URL for safe logging.

    Replaces actual password with '***' while preserving URL structure.
    """
    parsed = urllib.parse.urlparse(url)

    # If there's a password (and/or username), rebuild netloc with masked creds
    if parsed.password is not None:
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        # if only a password (no username),
        # parsed.username=="" → user=="" → ":***@host"
        credentials = f"{user}:***"
        netloc = f"{credentials}@{host}{port}"
    else:
        # no credentials present
        netloc = parsed.netloc

    safe_parsed = urllib.parse.ParseResult(
        scheme=parsed.scheme,
        netloc=netloc,
        path=parsed.path,
        params=parsed.params,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return safe_parsed.geturl()
