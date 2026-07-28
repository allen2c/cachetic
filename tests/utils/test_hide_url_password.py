"""Credential redaction for connection URLs.

``hide_url_password`` is what stands between a cache URL and every log line and
error message the library emits, so these pin both halves of its job: the
password never survives, and the function itself never raises.
"""

from cachetic.utils.hide_url_password import hide_url_password

SECRET = "TOPSECRET"


def test_password_is_masked():
    assert hide_url_password(f"redis://user:{SECRET}@localhost:6379/0") == "redis://user:***@localhost:6379/0"


def test_password_without_username_is_masked():
    assert hide_url_password(f"redis://:{SECRET}@localhost:6379/0") == "redis://:***@localhost:6379/0"


def test_password_containing_an_at_sign_is_masked():
    """The netloc has to be split on the *last* '@', not the first."""
    masked = hide_url_password(f"postgresql://user:p@{SECRET}@host:5432/db")
    assert masked == "postgresql://user:***@host:5432/db"
    assert SECRET not in masked


def test_url_without_credentials_is_untouched():
    url = "mongodb://localhost:27017/cachetic?collection=test"
    assert hide_url_password(url) == url


def test_username_without_password_is_untouched():
    url = "mongodb://user@localhost:27017/cachetic"
    assert hide_url_password(url) == url


def test_query_parameters_survive():
    """Cachetic's own params and libpq options must reach the log line intact."""
    masked = hide_url_password(f"postgresql://user:{SECRET}@host:5432/db?table=t&sslmode=require")
    assert masked == "postgresql://user:***@host:5432/db?table=t&sslmode=require"


def test_ipv6_host_keeps_its_brackets():
    """Rebuilding the netloc from ``parsed.hostname`` drops them.

    Without the brackets the address and the port merge into one run of
    colon-separated hex — the masked URL stops being a URL at all.
    """
    masked = hide_url_password(f"redis://:{SECRET}@[2001:db8::1]:6379/0")
    assert masked == "redis://:***@[2001:db8::1]:6379/0"


def test_non_numeric_port_does_not_raise():
    """``parsed.port`` raises on a bad port; a redaction helper must not.

    Both URL parsers mask the URL to build their own "invalid URL" message
    *before* validating it, so raising here replaces their diagnostic with an
    unrelated one from inside urllib.
    """
    masked = hide_url_password(f"postgresql://user:{SECRET}@host:notaport/db")
    assert SECRET not in masked
    assert "notaport" in masked


def test_unparsable_url_does_not_leak():
    """A URL urlparse rejects outright must not be echoed back verbatim."""
    assert hide_url_password(f"http://[::1@x:{SECRET}") == "<unparsable url>"


def test_filesystem_path_is_untouched():
    assert hide_url_password("/var/tmp/.cachetic") == "/var/tmp/.cachetic"
