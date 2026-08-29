"""Validation tests for request models (no browser required)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from robotsix_browser.models import ClickRequest, SelectRequest, WaitRequest


def test_click_requires_selector_or_role() -> None:
    with pytest.raises(ValidationError):
        ClickRequest()
    assert ClickRequest(selector="#go").selector == "#go"
    assert ClickRequest(role="button", name="Save").role == "button"


def test_select_requires_value_or_label() -> None:
    with pytest.raises(ValidationError):
        SelectRequest(selector="#s")
    assert SelectRequest(selector="#s", value="v").value == "v"


def test_wait_requires_selector_or_state() -> None:
    with pytest.raises(ValidationError):
        WaitRequest()
    assert WaitRequest(state="load").state == "load"
