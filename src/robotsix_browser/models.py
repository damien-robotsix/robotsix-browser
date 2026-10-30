"""Request / response models for the robotsix-browser HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]
LoadState = Literal["load", "domcontentloaded", "networkidle"]


class OpenSessionRequest(BaseModel):
    """Open a new session, or reuse the one identified by ``session_id``."""

    session_id: str | None = None


class SessionResponse(BaseModel):
    """Identifier of an opened (or reused) browser session."""

    session_id: str


class ActionResponse(BaseModel):
    """Generic response for a mutating page action."""

    status: str = "ok"
    url: str


class NavigateRequest(BaseModel):
    """Navigate to ``url`` (``http``/``https``/``data``/``about`` only; else 400).

    ``wait_until`` selects the load event the navigation waits for.
    """

    url: str
    wait_until: WaitUntil = "load"


class ClickRequest(BaseModel):
    """Click a target identified by a CSS selector or an ARIA role/name."""

    selector: str | None = None
    role: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _require_target(self) -> ClickRequest:
        if not self.selector and not self.role:
            raise ValueError("provide either 'selector' or 'role'")
        return self


class TypeRequest(BaseModel):
    """Fill the field matched by ``selector`` with ``text`` (no submit)."""

    selector: str
    text: str


class SelectRequest(BaseModel):
    """Select an ``<option>`` by value or by visible label."""

    selector: str
    value: str | None = None
    label: str | None = None

    @model_validator(mode="after")
    def _require_choice(self) -> SelectRequest:
        if self.value is None and self.label is None:
            raise ValueError("provide either 'value' or 'label'")
        return self


class UploadRequest(BaseModel):
    """Attach a file-hub file to a ``<input type=file>``."""

    selector: str
    file_id: str


#: Which part of the vault entry a ``fill-credentials`` call types.  ``both``
#: is the classic single-screen form; ``username`` / ``password`` serve
#: two-phase logins (identifier screen, then a separate password screen) where
#: only one of the two fields exists on the page at a time.
CredentialFields = Literal["both", "username", "password"]


class FillCredentialsRequest(BaseModel):
    """Fill a login form with a scoped Vaultwarden entry.

    The ``entry`` (a vault entry name or id) is resolved server-side via the
    scoped Bitwarden JSON API client; the fetched ``username`` / ``password``
    are typed directly into the given fields.  The secret is never echoed back —
    the response only
    reports success and the current page URL.  Filling does NOT submit: the
    separate human-gated ``/submit`` endpoint remains the only submit path.

    ``fields`` selects what is typed: ``both`` (default) fills username and
    password on one screen; ``username`` or ``password`` fills just that field
    so a two-phase login (email first, password on the next screen) can be
    driven as two calls around the intermediate "continue" click.  The
    selectors are optional hints — each is the first candidate tried, ahead
    of the built-in detection fallbacks — and a selector for a field that is
    not being filled is ignored.
    """

    entry: str
    fields: CredentialFields = "both"
    username_selector: str | None = None
    password_selector: str | None = None


class WaitRequest(BaseModel):
    """Wait for a selector and/or a page load state."""

    selector: str | None = None
    state: LoadState | None = None
    timeout_ms: int | None = None

    @model_validator(mode="after")
    def _require_condition(self) -> WaitRequest:
        if not self.selector and self.state is None:
            raise ValueError("provide either 'selector' or 'state'")
        return self


class ValueResponse(BaseModel):
    """Current value of the field matched by ``selector`` (404 if none matches)."""

    selector: str
    value: str


class VaultCollectionInfo(BaseModel):
    """Non-secret metadata for a vault collection visible to the scoped key.

    Deliberately exposes only ``id`` and ``name`` — never any secret value.
    """

    id: str
    name: str


class VaultCollectionsResponse(BaseModel):
    """Read-only collection metadata visible to the scoped API key."""

    collections: list[VaultCollectionInfo]


class VaultItemInfo(BaseModel):
    """Non-secret metadata for a vault item visible to the scoped key.

    Deliberately excludes every secret-bearing field (``login.password``,
    secure-note contents, hidden custom-field values, ...) so the response
    schema can never carry a secret.
    """

    id: str
    name: str


class VaultItemsResponse(BaseModel):
    """Read-only item metadata visible to the scoped API key."""

    items: list[VaultItemInfo]


class StateResponse(BaseModel):
    """Current page state: ARIA accessibility tree + full-page screenshot.

    ``accessibility_tree`` is Playwright's ARIA snapshot rendered as YAML.
    """

    url: str
    title: str
    accessibility_tree: str
    screenshot_base64: str


class SubmitRequest(BaseModel):
    """The human-gated final submit / confirm action.

    This is intentionally a *separate* model + endpoint from :class:`ClickRequest`
    so the operator can gate the consequential submission in-conversation.
    """

    selector: str | None = None
    role: str = "button"
    name: str | None = None
    wait_for_navigation: bool = True
