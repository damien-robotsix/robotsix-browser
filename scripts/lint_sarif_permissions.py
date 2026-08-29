#!/usr/bin/env python3
"""SARIF-upload permission validator for GitHub Actions workflows.

Checks that every job using a SARIF-uploading reusable workflow
declares ``security-events: write`` either at the job level or
inherits it from the workflow-level permissions block.  Without this
permission GitHub silently rejects the workflow file with a
0-second startup_failure.

Usage as a standalone script (called from CI workflow)::

    SARIF_WORKFLOWS="codeql.yml scan-container.yml" \
        python3 scripts/lint_sarif_permissions.py

Usage as an importable module (called from tests)::

    from scripts.lint_sarif_permissions import check
    exit_code = check(sarif_workflows={"codeql.yml"}, workflow_dir=".github/workflows")
"""

from __future__ import annotations

import glob
import os
import sys

try:
    import yaml
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "pyyaml"])
    import yaml


def _has_se_write(perms: object) -> bool:
    """Return True if *perms* grants security-events:write."""
    if isinstance(perms, dict):
        return perms.get("security-events", "") == "write"
    if isinstance(perms, str):
        return perms == "write-all"
    # None → inherit; empty/other → deny
    return False


def check(
    *,
    sarif_workflows: set[str] | None = None,
    workflow_dir: str = ".github/workflows",
) -> int:
    """Validate SARIF-upload permissions across all workflow files.

    Returns 0 when all SARIF-using jobs have correct permissions, 1
    when violations are found.
    """
    if sarif_workflows is None:
        sarif_workflows = set()

    if not sarif_workflows:
        print("::notice::No SARIF workflows configured; nothing to check.")
        return 0

    if not os.path.isdir(workflow_dir):
        print(f"::notice::{workflow_dir} not found; nothing to check.")
        return 0

    errors: list[str] = []
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

        root_perms = doc.get("permissions", {})
        # A missing root permissions block (None) means "inherit
        # org/repo defaults" — effectively permissive.  An empty
        # dict {} means "none".  The string "write-all" grants
        # write to everything including security-events.
        root_has_security_events: bool | None = None
        if isinstance(root_perms, dict):
            root_se = root_perms.get("security-events", "")
            if root_se == "write":
                root_has_security_events = True
            elif root_se == "none" or root_se == "read":
                root_has_security_events = False
        elif isinstance(root_perms, str):
            root_has_security_events = root_perms == "write-all"

        for job_id, job in doc["jobs"].items():
            if not isinstance(job, dict):
                continue
            # Check whether this job uses any SARIF workflow.
            uses_sarif = False
            steps = job.get("steps", [])
            if not isinstance(steps, list):
                steps = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses", "")
                if not isinstance(uses, str):
                    continue
                basename = uses.rsplit("/", 1)[-1]
                # Strip any @ref suffix (e.g. python-ci.yml@main →
                # python-ci.yml) so we match on the workflow filename.
                if "@" in basename:
                    basename = basename.split("@", 1)[0]
                if basename in sarif_workflows:
                    uses_sarif = True
                    break

            if not uses_sarif:
                continue

            job_perms = job.get("permissions")
            se_write = False

            if job_perms is not None:
                se_write = _has_se_write(job_perms)
            elif root_has_security_events is not None:
                se_write = root_has_security_events
            else:
                # No permissions declared anywhere → defaults
                # are permissive, so security-events is write.
                se_write = True

            if not se_write:
                errors.append(
                    f"{path}: job '{job_id}' uses a SARIF-uploading "
                    f"reusable workflow but does not grant "
                    f"security-events:write (neither at job nor "
                    f"workflow level)."
                )

    if errors:
        for msg in errors:
            print(f"::error file={msg.split(':')[0]}::{msg}", file=sys.stderr)
        return 1

    print("::notice::All SARIF-uploading jobs declare security-events:write.")
    return 0


if __name__ == "__main__":
    sarif_workflows_raw = os.environ.get("SARIF_WORKFLOWS", "")
    sarif_set = {w.strip() for w in sarif_workflows_raw.split() if w.strip()}
    sys.exit(check(sarif_workflows=sarif_set))
