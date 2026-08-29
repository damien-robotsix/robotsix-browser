"""Vulture whitelist.

Symbols referenced here are intentionally kept alive for vulture, which cannot
see uses that happen through frameworks (FastAPI route registration, pydantic
model fields populated from request bodies, etc.).
"""

from robotsix_browser.config import Settings
from robotsix_browser.models import (
    ActionResponse,
    SelectRequest,
    StateResponse,
    SubmitRequest,
    ValueResponse,
    WaitRequest,
)

# pydantic model fields are populated dynamically from request JSON.
_settings = Settings()
_ = _settings.default_timeout_ms
_ = ActionResponse(url="").status
_ = StateResponse(url="", title="", accessibility_tree="", screenshot_base64="")
_ = ValueResponse(selector="", value="")
_ = SelectRequest(selector="", value="").label
_ = WaitRequest(state="load").timeout_ms
_ = SubmitRequest().wait_for_navigation
