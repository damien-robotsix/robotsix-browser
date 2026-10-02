"""Tests for the Dockerfile digest-pin linter.

``scripts/lint_dockerfile_pins.check`` enforces audit finding #2: every
``FROM`` base image and ``COPY --from`` tool image must be pinned by an
immutable ``@sha256:`` digest so a mutable floating tag (``:latest``,
``:slim``, ``:main``) can never slip back in.  These tests exercise the
linter against synthetic Dockerfiles and assert the repository's own
Dockerfile already passes.
"""

from __future__ import annotations

from pathlib import Path

from scripts.lint_dockerfile_pins import check

REPO_ROOT = Path(__file__).resolve().parent.parent

_DIGEST = "@sha256:" + "0" * 64


def test_repo_dockerfile_is_pinned() -> None:
    """The committed Dockerfile must already be fully digest-pinned."""
    assert check(dockerfiles=[str(REPO_ROOT / "Dockerfile")]) == 0


def test_floating_from_tag_fails(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.14-slim\n")
    assert check(dockerfiles=[str(dockerfile)]) == 1


def test_latest_from_tag_fails(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:latest\n")
    assert check(dockerfiles=[str(dockerfile)]) == 1


def test_floating_copy_from_fails(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM python:3.14-slim{_DIGEST}\n"
        "COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/\n"
    )
    assert check(dockerfiles=[str(dockerfile)]) == 1


def test_pinned_images_pass(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM python:3.14-slim{_DIGEST}\n"
        f"COPY --from=ghcr.io/astral-sh/uv:0.12.22{_DIGEST} /uv /uvx /bin/\n"
    )
    assert check(dockerfiles=[str(dockerfile)]) == 0


def test_build_stage_reference_is_exempt(tmp_path: Path) -> None:
    """A COPY --from referencing a named build stage is not an image."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        f"FROM python:3.14-slim{_DIGEST} AS builder\n"
        "RUN echo build\n"
        f"FROM python:3.14-slim{_DIGEST}\n"
        "COPY --from=builder /app /app\n"
    )
    assert check(dockerfiles=[str(dockerfile)]) == 0


def test_scratch_base_is_exempt(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    assert check(dockerfiles=[str(dockerfile)]) == 0


def test_missing_dockerfile_is_noop(tmp_path: Path) -> None:
    assert check(dockerfiles=[str(tmp_path / "Nope")]) == 0
