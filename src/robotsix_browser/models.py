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
    session_id: str


class ActionResponse(BaseModel):
    """Generic response for a mutating page action."""

    status: str = "ok"
    url: str


class NavigateRequest(BaseModel):
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


class FillCredentialsRequest(BaseModel):
    """Fill a login form with a scoped Vaultwarden entry.

    The ``entry`` (a vault entry name or id) is resolved server-side via the
    Bitwarden CLI; the fetched ``username`` / ``password`` are typed directly
    into the given fields.  The secret is never echoed back — the response only
    reports success and the current page URL.  Filling does NOT submit: the
    separate human-gated ``/submit`` endpoint remains the only submit path.
    """

    entry: str
    username_selector: str
    password_selector: str


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
    selector: str
    value: str


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
