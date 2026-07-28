"""Executes every ``python`` code block in README.md.

The rule this pins: **every ```` ```python ```` fence in `README.md` runs.**
Blocks that only illustrate something — a before/after diff, a shell snippet —
use a different fence language and are left alone.

Blocks are executed in order into one shared namespace, the way a reader goes
through the page: a class defined in Quick Start is still there in the Async
section. That also means a snippet cannot quietly depend on a name the README
never introduces.

Everything runs against the disk backend in a temporary working directory, so no
service is needed and nothing is written into the repository. Snippets that name
Redis, MongoDB or PostgreSQL only *construct* a client — connecting is lazy — so
they are exercised as far as they can be without those services.
"""

import pathlib
import re

import pytest

README = pathlib.Path(__file__).parent.parent.joinpath("README.md")

PYTHON_BLOCK = re.compile(r"^```python\n(.*?)^```", re.MULTILINE | re.DOTALL)


def python_blocks() -> list[str]:
    """Returns every ``python`` fenced block in README.md, in document order."""
    return PYTHON_BLOCK.findall(README.read_text(encoding="utf-8"))


def test_readme_has_python_blocks():
    """Guards the extraction itself.

    If the regex ever stops matching, every assertion below would pass by
    executing nothing at all.
    """
    assert len(python_blocks()) >= 10


def test_readme_python_blocks_execute(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Every README snippet must run top to bottom without raising."""
    monkeypatch.chdir(tmp_path)
    # Cachetic reads CACHETIC_-prefixed settings from the environment; a value
    # left over from the developer's shell would change what the snippets do.
    for name in ("CACHE_URL", "DEFAULT_TTL", "PREFIX", "COMPRESSION"):
        monkeypatch.delenv(f"CACHETIC_{name}", raising=False)

    namespace: dict[str, object] = {"__name__": "__readme__"}

    for index, block in enumerate(python_blocks()):
        source = f"README.md::python[{index}]"
        try:
            exec(compile(block, source, "exec"), namespace)
        except Exception as error:
            pytest.fail(f"{source} raised {type(error).__name__}: {error}\n\n{block}")
