"""Importing Cachetic must not import a backend driver.

[Principle 5](../docs/PRINCIPLES.md): `pip install cachetic` works alone, and no
backend package is imported until its URL scheme is used. Disk is the exception
the rule names — `diskcache` ships in the base install because it is the default
backend — but even that must not load until something asks for it.

Every one of these runs in a **subprocess**. An in-process check is worthless:
the rest of this suite imports redis, pymongo and psycopg at module scope, so by
the time any assertion ran they would already be in `sys.modules` and the test
would pass whatever the library did.

This is what nothing pinned before. The property held — it was checked by hand
more than once — and one module-scope `import redis` for a type annotation is all
it takes to break it for everyone who never installed the extra.
"""

import subprocess
import sys

import pytest

OPTIONAL_DRIVERS = ["redis", "pymongo", "psycopg", "psycopg_pool", "asyncpg", "motor"]
"""Backend packages that must be absent from a fresh import."""

LAZY_DRIVERS = [*OPTIONAL_DRIVERS, "diskcache", "zstandard"]
"""Plus the two that ship in the base install but still load on demand."""

IMPORTABLE = [
    "cachetic",
    "cachetic.aio",
    "cachetic.extensions",
    "cachetic.types",
]


def modules_loaded_after_importing(module: str, candidates: list[str]) -> list[str]:
    """Returns which of `candidates` a fresh interpreter loaded, importing `module`."""
    program = "import sys;" f"import {module};" f"print(','.join(m for m in {candidates!r} if m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.strip().split(",") if name]


@pytest.mark.parametrize("module", IMPORTABLE)
def test_importing_cachetic_loads_no_backend_driver(module: str):
    assert modules_loaded_after_importing(module, LAZY_DRIVERS) == []


def test_building_a_client_still_loads_nothing():
    """Configuration is not connection. The adapter is built on first use."""
    program = (
        "import sys, pathlib, pydantic, cachetic;"
        "cachetic.Cachetic[str](object_type=pydantic.TypeAdapter(str), cache_url='redis://localhost:6379/0');"
        f"print(','.join(m for m in {LAZY_DRIVERS!r} if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""


def test_touching_the_disk_backend_loads_diskcache_and_nothing_else():
    """The other half of the rule: the driver for the scheme in use *does* load."""
    program = (
        "import sys, tempfile, pathlib, pydantic, cachetic;"
        "d = tempfile.mkdtemp();"
        "c = cachetic.Cachetic[str](object_type=pydantic.TypeAdapter(str),"
        " cache_url=pathlib.Path(d).joinpath('.cachetic'));"
        "c.set('k', 'v');"
        "print('diskcache' in sys.modules, ','.join(m for m in "
        f"{OPTIONAL_DRIVERS!r} if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
    loaded_diskcache, optional = (
        result.stdout.strip().split(" ", 1)
        if " " in result.stdout.strip()
        else (
            result.stdout.strip(),
            "",
        )
    )
    assert loaded_diskcache == "True", "the disk backend did not load diskcache"
    assert optional == "", f"an unrelated driver was pulled in: {optional}"
