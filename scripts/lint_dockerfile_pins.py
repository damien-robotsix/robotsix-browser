#!/usr/bin/env python3
"""Digest-pin validator for Dockerfile base and tool images.

Audit finding #2 pinned every ``FROM`` base image and ``COPY --from``
tool image to an immutable ``@sha256:`` digest.  This linter blocks any
regression back to a mutable floating tag (e.g. ``:latest``, ``:slim``,
``:main``): every image reference in a ``FROM`` or ``COPY --from=``
clause must carry a ``@sha256:`` digest.

References to an earlier build stage (``FROM <stage>``,
``COPY --from=<stage>`` or ``COPY --from=0``) and the special
``scratch`` base are not images and are exempt.  References that
interpolate a build ``ARG``/``ENV`` (contain ``$``) cannot be verified
statically and are skipped.

Usage as a standalone script::

    python3 scripts/lint_dockerfile_pins.py [Dockerfile ...]

Usage as an importable module::

    from scripts.lint_dockerfile_pins import check
    exit_code = check(dockerfiles=["Dockerfile"])
"""

from __future__ import annotations

import os
import re
import sys

_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Yield ``(line_number, instruction)`` with backslash continuations
    joined and blank / comment lines dropped.

    *line_number* is the 1-based number of the line that starts the
    instruction.
    """
    lines: list[tuple[int, str]] = []
    buffer = ""
    start_no = 0
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start_no = idx
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        lines.append((start_no, buffer.strip()))
        buffer = ""
    if buffer:
        lines.append((start_no, buffer.strip()))
    return lines


def _is_pinned(ref: str) -> bool:
    return bool(_DIGEST_RE.search(ref))


def _check_dockerfile(path: str, errors: list[str]) -> None:
    with open(path) as fh:
        text = fh.read()

    stages: set[str] = set()
    for line_no, instruction in _logical_lines(text):
        tokens = instruction.split()
        if not tokens:
            continue
        keyword = tokens[0].upper()

        if keyword == "FROM":
            idx = 1
            while idx < len(tokens) and tokens[idx].startswith("--"):
                idx += 1
            if idx >= len(tokens):
                continue
            image = tokens[idx]
            # Register a declared build-stage alias (`FROM ... AS name`).
            if idx + 2 < len(tokens) and tokens[idx + 1].upper() == "AS":
                stages.add(tokens[idx + 2].lower())
            if "$" in image:
                continue
            if image.lower() == "scratch" or image.lower() in stages:
                continue
            if not _is_pinned(image):
                errors.append(
                    f"{path}:{line_no}: FROM image '{image}' is not pinned "
                    f"by digest — use '<image>:<tag>@sha256:<64-hex>' to "
                    f"avoid a mutable floating tag."
                )

        elif keyword == "COPY":
            for token in tokens[1:]:
                if not token.startswith("--from="):
                    continue
                ref = token[len("--from=") :]
                if "$" in ref:
                    continue
                # A build-stage alias or numeric stage index is not an image.
                if ref.lower() in stages or ref.isdigit():
                    continue
                if not _is_pinned(ref):
                    errors.append(
                        f"{path}:{line_no}: COPY --from image '{ref}' is not "
                        f"pinned by digest — use "
                        f"'<image>:<tag>@sha256:<64-hex>' to avoid a mutable "
                        f"floating tag."
                    )


def check(*, dockerfiles: list[str] | None = None) -> int:
    """Validate that every Dockerfile image reference is digest-pinned.

    Returns 0 when every ``FROM`` / ``COPY --from`` image carries a
    ``@sha256:`` digest, 1 when a floating (unpinned) reference is found.
    """
    paths = dockerfiles if dockerfiles is not None else ["Dockerfile"]
    errors: list[str] = []
    for path in paths:
        if not os.path.isfile(path):
            print(f"::notice::{path} not found; nothing to check.")
            continue
        _check_dockerfile(path, errors)

    if errors:
        for msg in errors:
            print(f"::error file={msg.split(':')[0]}::{msg}", file=sys.stderr)
        return 1

    print("::notice::All Dockerfile images are pinned by digest.")
    return 0


if __name__ == "__main__":
    sys.exit(check(dockerfiles=sys.argv[1:] or None))
