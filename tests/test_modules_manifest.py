"""Deterministic completeness check for ``docs/modules.yaml``.

``docs/modules.yaml`` is the canonical module-file manifest and claims that
every source / test / changelog file is registered under a module.  Nothing
previously consumed it, so the manifest silently drifted from the tree.  These
tests turn that hand-sync into a CI failure: the union of every module ``paths``
entry and every ``exemptions`` pattern must exactly cover the git-tracked tree —
no tracked file left unregistered, no manifest entry pointing at a file that no
longer exists.

The manifest is parsed line-by-line (collecting every ``- <token>`` list item)
rather than with PyYAML: PyYAML is not a project dependency, and the manifest's
list-of-strings shape makes a dependency-free parse reliable.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "docs" / "modules.yaml"

# Matches a YAML list item whose value is a single bare token, e.g.
# ``- src/robotsix_browser/app.py`` or ``- changelog.d/*.md``.  Multi-token
# items such as ``- name: core`` (the module list) deliberately do not match.
_LIST_ITEM = re.compile(r"^\s*-\s+(\S+)\s*$")


def _manifest_patterns() -> list[str]:
    """Return every path / glob pattern declared in the manifest."""
    patterns: list[str] = []
    for line in MANIFEST.read_text().splitlines():
        match = _LIST_ITEM.match(line)
        if match:
            patterns.append(match.group(1))
    return patterns


def _tracked_files() -> set[str]:
    """Return the set of git-tracked paths (repo-root-relative, POSIX)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def _matches(path: str, pattern: str) -> bool:
    return path == pattern or fnmatch.fnmatch(path, pattern)


def test_manifest_has_patterns() -> None:
    """Guard against a parser regression silently matching nothing."""
    assert _manifest_patterns(), "No path entries parsed from docs/modules.yaml"


def test_every_tracked_file_is_registered() -> None:
    patterns = _manifest_patterns()
    unregistered = sorted(
        path
        for path in _tracked_files()
        if not any(_matches(path, pattern) for pattern in patterns)
    )
    assert not unregistered, (
        "Tracked files missing from docs/modules.yaml. Add each under the "
        "appropriate module's `paths:` (or to `exemptions:` if it is not "
        f"module content): {unregistered}"
    )


def test_no_stale_manifest_entries() -> None:
    tracked = _tracked_files()
    stale = sorted(
        pattern
        for pattern in _manifest_patterns()
        if not any(_matches(path, pattern) for path in tracked)
    )
    assert not stale, (
        "docs/modules.yaml lists paths/patterns that match no tracked file "
        f"(remove them or fix the path): {stale}"
    )
