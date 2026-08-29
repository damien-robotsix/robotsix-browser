"""HTTP-level tests.

The health/404 tests run without a browser.  The smoke and upload tests drive
real headless Chromium and are skipped (via the ``browser_available`` fixture)
when no browser binary is installed.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

_FORM_HTML = (
    "<form><input id='name'>"
    "<select id='color'><option value='r'>Red</option>"
    "<option value='g'>Green</option></select>"
    "<input id='file' type='file'></form>"
)


def _data_url(html: str) -> str:
    return "data:text/html," + quote(html)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.post(
        "/sessions/does-not-exist/navigate", json={"url": "data:text/html,x"}
    )
    assert response.status_code == 404


def test_smoke_fill_and_read_back(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]

    nav = client.post(
        f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)}
    )
    assert nav.status_code == 200

    typed = client.post(
        f"/sessions/{session_id}/type", json={"selector": "#name", "text": "Ada"}
    )
    assert typed.status_code == 200

    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#name"})
    assert value.json() == {"selector": "#name", "value": "Ada"}


def test_state_returns_tree_and_screenshot(
    browser_available: None, client: TestClient
) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    state = client.get(f"/sessions/{session_id}/state").json()
    assert "screenshot_base64" in state
    assert state["screenshot_base64"]
    assert "accessibility_tree" in state


def test_select_option(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    client.post(
        f"/sessions/{session_id}/select", json={"selector": "#color", "value": "g"}
    )
    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#color"})
    assert value.json()["value"] == "g"


def test_upload_from_file_hub(browser_available: None, client: TestClient) -> None:
    session_id = client.post("/sessions", json={}).json()["session_id"]
    client.post(f"/sessions/{session_id}/navigate", json={"url": _data_url(_FORM_HTML)})

    upload = client.post(
        f"/sessions/{session_id}/upload",
        json={"selector": "#file", "file_id": "file-123"},
    )
    assert upload.status_code == 200

    # A populated file input reports the attached filename via its value.
    value = client.get(f"/sessions/{session_id}/value", params={"selector": "#file"})
    assert value.json()["value"].endswith("upload.txt")
