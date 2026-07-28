"""Executes every ``python`` code block in the two user-facing pages.

The rule this pins: **every ```` ```python ```` fence in `README.md` and in
`docs/index.md` runs.** Blocks that only illustrate something — a before/after
diff, a shell snippet — use a different fence language and are left alone.

`docs/contributing.md` says the two pages carry the same content in two formats:
plain Markdown for GitHub, mkdocs-material tabs and admonitions for the site.
They are therefore not identical files and cannot be compared line for line —
`docs/index.md` legitimately has more blocks, because a tabbed section shows the
same call twice. What they can be held to is that both actually run. Until this
covered `docs/index.md`, only the README half was ever executed, and the copy
that the documentation site serves could rot on its own.

Blocks are executed in order into one shared namespace per page, the way a reader
goes through it: a class defined in Quick Start is still there in the Async
section. That also means a snippet cannot quietly depend on a name the page never
introduces — which is the check that catches a tab lifted from elsewhere.

Everything runs against the disk backend in a temporary working directory, so no
service is needed and nothing is written into the repository. Snippets that name
Redis, MongoDB or PostgreSQL only *construct* a client — connecting is lazy — so
they are exercised as far as they can be without those services.
"""

import pathlib
import re
import textwrap

import pytest

DOCS_ROOT = pathlib.Path(__file__).parent.parent

PAGES = ["README.md", "docs/index.md"]

PYTHON_BLOCK = re.compile(r"^(?P<indent>[ \t]*)```python\n(?P<body>.*?)^(?P=indent)```", re.MULTILINE | re.DOTALL)
"""Matches an indented fence too — mkdocs tabs indent their content four spaces."""


def python_blocks(page: str) -> list[str]:
    """Returns every ``python`` fenced block in ``page``, in document order.

    Dedented, because a block nested inside a mkdocs tab or admonition is
    indented in the source and would not compile as written.
    """
    text = DOCS_ROOT.joinpath(page).read_text(encoding="utf-8")
    return [textwrap.dedent(match.group("body")) for match in PYTHON_BLOCK.finditer(text)]


@pytest.mark.parametrize("page", PAGES)
def test_page_has_python_blocks(page: str):
    """Guards the extraction itself.

    If the regex ever stops matching, every assertion below would pass by
    executing nothing at all.
    """
    assert len(python_blocks(page)) >= 10


@pytest.mark.parametrize("page", PAGES)
def test_page_python_blocks_execute(page: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Every snippet must run top to bottom without raising."""
    monkeypatch.chdir(tmp_path)
    # Cachetic reads CACHETIC_-prefixed settings from the environment; a value
    # left over from the developer's shell would change what the snippets do.
    for name in ("CACHE_URL", "DEFAULT_TTL", "PREFIX", "COMPRESSION"):
        monkeypatch.delenv(f"CACHETIC_{name}", raising=False)

    namespace: dict[str, object] = {"__name__": "__docs__"}

    for index, block in enumerate(python_blocks(page)):
        source = f"{page}::python[{index}]"
        try:
            exec(compile(block, source, "exec"), namespace)
        except Exception as error:
            pytest.fail(f"{source} raised {type(error).__name__}: {error}\n\n{block}")
