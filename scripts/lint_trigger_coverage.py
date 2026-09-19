#!/usr/bin/env python3
"""Trigger-coverage validator for GitHub Actions workflows.

Checks that every job's ``if:`` condition does not exclude ALL declared
workflow triggers.  When a job gates on ``github.event_name`` and the
condition can never be true for any of the ``on:`` events, the job can
never execute — flag it as dead code.

Usage as a standalone script::

    python3 scripts/lint_trigger_coverage.py

Usage as an importable module::

    from scripts.lint_trigger_coverage import check
    exit_code = check(workflow_dir=".github/workflows")
"""

from __future__ import annotations

import os
import re
import sys

try:
    from _workflow_lint import iter_workflow_docs, report
except ImportError:  # imported as ``scripts.lint_trigger_coverage``
    from scripts._workflow_lint import iter_workflow_docs, report

# ---------------------------------------------------------------------------
# Patterns for extracting event-name comparisons from if: expressions
# ---------------------------------------------------------------------------

_EVENT_EQ_RE = re.compile(r"github\.event_name\s*==\s*'([^']+)'")
_EVENT_NEQ_RE = re.compile(r"github\.event_name\s*!=\s*'([^']+)'")


def _extract_event_names(on_block: object) -> set[str]:
    """Return the set of event names declared in the ``on:`` block."""
    events: set[str] = set()

    if on_block is None:
        return events

    if isinstance(on_block, str):
        events.add(on_block)
    elif isinstance(on_block, list):
        for item in on_block:
            if isinstance(item, str):
                events.add(item)
    elif isinstance(on_block, dict):
        for key in on_block:
            if isinstance(key, str):
                events.add(key)
    return events


def check(*, workflow_dir: str = ".github/workflows") -> int:
    """Validate trigger coverage across all workflow files.

    Returns 0 when every job's ``if:`` condition is satisfiable by at
    least one declared trigger, 1 when violations are found.
    """
    if not os.path.isdir(workflow_dir):
        print(f"::notice::{workflow_dir} not found; nothing to check.")
        return 0

    errors: list[str] = []
    for path, doc in iter_workflow_docs(workflow_dir, errors):
        triggers = _extract_event_names(doc.get("on"))
        if not triggers:
            continue

        for job_id, job in doc["jobs"].items():
            if not isinstance(job, dict):
                continue
            if_expr = job.get("if", "")
            if not if_expr or not isinstance(if_expr, str):
                continue

            # github.event_name == 'X' — flag when X is not a declared trigger
            for m in _EVENT_EQ_RE.finditer(if_expr):
                target = m.group(1)
                if target not in triggers:
                    errors.append(
                        f"{path}: job '{job_id}' has "
                        f"`if: github.event_name == '{target}'` "
                        f"but '{target}' is not a declared "
                        f"workflow trigger (on: {sorted(triggers)})."
                    )

            # github.event_name != 'X' — flag when X is the ONLY trigger
            for m in _EVENT_NEQ_RE.finditer(if_expr):
                target = m.group(1)
                if triggers == {target}:
                    errors.append(
                        f"{path}: job '{job_id}' has "
                        f"`if: github.event_name != '{target}'` "
                        f"but '{target}' is the ONLY declared "
                        f"workflow trigger — the job can never run."
                    )

    return report(
        errors, "All job if: conditions are satisfiable by declared triggers."
    )


if __name__ == "__main__":
    sys.exit(check())
