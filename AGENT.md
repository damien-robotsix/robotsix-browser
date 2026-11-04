# robotsix-browser — agent guidance

This repo follows the [robotsix stack standards](https://github.com/damien-robotsix/robotsix-standards).

## Scope

Interactive headless-browser / form-filling HTTP service: a FastAPI app
(`src/robotsix_browser/app.py`) wrapping Playwright headless Chromium behind a
session-scoped API. See [README.md](README.md) for the API and
[docs/architecture.md](docs/architecture.md) for the request flow.

## Hard rules

- **Human submit-gate.** Only `POST /sessions/{id}/submit` may submit a form.
  Never make any other endpoint submit, and never wire an automatic call to
  `/submit` after filling a form.
- **Secrets never leave the server.** Vaultwarden credentials fetched by
  `fill-credentials` are typed directly into the page; they are never
  returned in a response or logged.
- **Config schema sync.** When changing the `Settings` model in
  `src/robotsix_browser/config.py`, update `docs/configuration.md` and
  regenerate `config/config.schema.json` in the same change (CI enforces it;
  the command is in the README).
- **Module registration.** Keep `docs/modules.yaml` in sync with the source
  tree (CI runs `robotsix-modules check-registration`).

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run deptry .
uv run vulture --ignore-decorators "@field_validator,@model_validator" src/ vulture_whitelist.py
uv run pytest
```
