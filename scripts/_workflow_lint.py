#!/usr/bin/env python3
"""Shared scaffolding for CI-invoked GitHub Actions workflow linters.

Both :mod:`lint_sarif_permissions` and :mod:`lint_trigger_coverage`
iterate over the same workflow files, parse them the same way, and emit
GitHub annotations in the same format.  This module hosts that common
scaffolding so the two linters stay consistent:

* a ``yaml`` import with a runtime ``pip install pyyaml`` bootstrap for
  the standalone-CI invocation where PyYAML may be absent;
* :func:`iter_workflow_docs` — yield ``(path, doc)`` for every workflow
  file that parses to a mapping containing a ``jobs`` block;
* :func:`report` — print the GitHub ``::error``/``::notice`` annotations
  and return the process exit code.
"""

from __future__ import annotations

import glob
import sys
from collections.abc import Iterator
from typing import Any

try:
    import yaml
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pyyaml"])
    import yaml


def iter_workflow_docs(
    workflow_dir: str, errors: list[str]
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(path, doc)`` for each workflow file under *workflow_dir*.

    Files are visited in sorted order across both ``*.yml`` and
    ``*.yaml`` extensions.  A file that fails to parse appends an
    ``invalid YAML`` message to *errors* and is skipped.  Files that do
    not parse to a mapping containing a ``jobs`` key are skipped
    silently.
    """
    for path in sorted(
        glob.glob(f"{workflow_dir}/*.yml") + glob.glob(f"{workflow_dir}/*.yaml")
    ):
        with open(path) as fh:
            try:
                doc = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                errors.append(f"{path}: invalid YAML — {exc}")
                continue

        if not isinstance(doc, dict) or "jobs" not in doc:
            continue

        yield path, doc


def report(errors: list[str], success_message: str) -> int:
    """Emit GitHub annotations for *errors* and return the exit code.

    Prints one ``::error`` annotation per message and returns 1 when
    *errors* is non-empty; otherwise prints *success_message* as a
    ``::notice`` and returns 0.
    """
    if errors:
        for msg in errors:
            print(f"::error file={msg.split(':')[0]}::{msg}", file=sys.stderr)
        return 1

    print(f"::notice::{success_message}")
    return 0
