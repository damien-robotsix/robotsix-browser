"""robotsix-browser: interactive headless-browser / form-filling HTTP service.

The service wraps Playwright headless Chromium behind a small, session-scoped
HTTP API so an agent can drive a real browser to inspect and fill web forms.

HUMAN SUBMIT-GATE: the service never auto-submits a consequential form.  The
final submit / confirm action is exposed as a *separate* endpoint
(``POST /sessions/{id}/submit``) so an operator can gate it in-conversation.
See ``README.md`` for the full rule.
"""

from robotsix_browser.app import create_app

__all__ = ["create_app"]
