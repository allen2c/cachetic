import functools
import os
import pathlib
import socket
import tempfile
import typing
import urllib.parse

import logging_bullet_train
import pytest

# Backend URLs used by the test suite. Each is overridable via an env var so the
# suite can run against non-default ports or a remote host without code changes.
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_DEFAULT_MONGO_URL = "mongodb://localhost:27017/cachetic?collection=test"
_DEFAULT_POSTGRES_URL = "postgresql://postgres:postgres@localhost:5432/cachetic?table=test_cache"

_DEFAULT_PORTS: dict[str, int] = {
    "redis": 6379,
    "mongodb": 27017,
    "postgresql": 5432,
    "postgres": 5432,
}


@functools.cache
def is_service_available(host: str, port: int, timeout: float = 0.5) -> bool:
    """Returns True if a TCP connection to host:port succeeds.

    Cached so that repeated checks across a session cost one probe per endpoint.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def require_service(url: str, *, label: str) -> str:
    """Returns ``url``, or skips the current test if the service is unreachable.

    Keeps backend tests runnable on a machine without Redis/MongoDB/PostgreSQL
    instead of failing them with a connection error.
    """
    parsed: urllib.parse.ParseResult = urllib.parse.urlparse(url)
    host: str = parsed.hostname or "localhost"
    port: int = parsed.port or _DEFAULT_PORTS.get(parsed.scheme, 0)

    if not is_service_available(host, port):
        pytest.skip(f"{label} not reachable at {host}:{port} — " "start it with `docker compose up -d`")
    return url


def _url_from_env(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)


@pytest.fixture(scope="session", autouse=True)
def set_env_vars() -> typing.Iterator[None]:
    os.environ["ENVIRONMENT"] = "test"
    os.environ["PYTEST_RUNNING"] = "true"
    os.environ["PYTEST_IS_RUNNING"] = "true"

    logging_bullet_train.set_logger("cachetic")
    yield


@pytest.fixture(scope="function")
def temp_cache_url() -> typing.Iterator[pathlib.Path]:
    with tempfile.TemporaryDirectory() as temp_dir:
        yield pathlib.Path(temp_dir).joinpath(".cachetic")


@pytest.fixture(scope="module")
def redis_connection_string() -> str:
    return require_service(
        _url_from_env("CACHETIC_TEST_REDIS_URL", _DEFAULT_REDIS_URL),
        label="Redis",
    )


@pytest.fixture(scope="module")
def mongo_connection_string() -> str:
    return require_service(
        _url_from_env("CACHETIC_TEST_MONGO_URL", _DEFAULT_MONGO_URL),
        label="MongoDB",
    )


@pytest.fixture(scope="module")
def postgres_connection_string() -> str:
    return require_service(
        _url_from_env("CACHETIC_TEST_POSTGRES_URL", _DEFAULT_POSTGRES_URL),
        label="PostgreSQL",
    )


@pytest.fixture(params=["disk", "redis", "mongodb", "postgres"])
def backend_url(request: pytest.FixtureRequest, tmp_path: pathlib.Path) -> str | pathlib.Path:
    """A cache URL for each supported backend, one test run per backend.

    Lets a single set of assertions cover every backend, and to be reused
    verbatim by the sync and the async client. Unavailable services skip.
    """
    backend: str = request.param
    if backend == "disk":
        return tmp_path.joinpath(".cachetic")
    if backend == "redis":
        return require_service(
            _url_from_env("CACHETIC_TEST_REDIS_URL", _DEFAULT_REDIS_URL),
            label="Redis",
        )
    if backend == "mongodb":
        return require_service(
            _url_from_env("CACHETIC_TEST_MONGO_URL", _DEFAULT_MONGO_URL),
            label="MongoDB",
        )
    return require_service(
        _url_from_env("CACHETIC_TEST_POSTGRES_URL", _DEFAULT_POSTGRES_URL),
        label="PostgreSQL",
    )
