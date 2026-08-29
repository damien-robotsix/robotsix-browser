Bootstrap the **robotsix-browser** service: a session-scoped HTTP API wrapping
Playwright headless Chromium for interactive form-filling (navigate, inspect,
click, type, select, upload from file-hub, wait, read-back). The final
submit/confirm action is a separate, human-gated endpoint — no endpoint
auto-submits a form. Includes Dockerfile (installs Chromium), CI, and tests.
