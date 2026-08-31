"""FastAPI application wiring the browser-automation endpoints.

Endpoint map (all session-scoped, each session is an isolated browser context):

* ``POST   /sessions``                 open or reuse a session
* ``DELETE /sessions/{id}``            close a session
* ``POST   /sessions/{id}/navigate``   navigate to a URL
* ``GET    /sessions/{id}/state``      ARIA tree + full-page screenshot
* ``POST   /sessions/{id}/click``      click by role or selector
* ``POST   /sessions/{id}/type``       fill a text field
* ``POST   /sessions/{id}/select``     choose a ``<select>`` option
* ``POST   /sessions/{id}/upload``     attach a file-hub file to a file input
* ``POST   /sessions/{id}/wait``       wait for a selector / load state
* ``GET    /sessions/{id}/value``      read back a field's current value
* ``POST   /sessions/{id}/fill-credentials``  inject a scoped Vaultwarden entry
* ``POST   /sessions/{id}/submit``     HUMAN-GATED final submit / confirm

HUMAN SUBMIT-GATE: no endpoint other than ``/submit`` submits a form, and
``/submit`` exists solely so an operator can gate the consequential action.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from robotsix_browser import chat_skill, operations
from robotsix_browser.config import Settings, get_settings
from robotsix_browser.filehub import FileHubClient, FileHubError, InvalidFileIdError
from robotsix_browser.models import (
    ActionResponse,
    ClickRequest,
    FillCredentialsRequest,
    NavigateRequest,
    OpenSessionRequest,
    SelectRequest,
    SessionResponse,
    StateResponse,
    SubmitRequest,
    TypeRequest,
    UploadRequest,
    ValueResponse,
    WaitRequest,
)
from robotsix_browser.operations import UnsupportedUrlError
from robotsix_browser.sessions import Session, SessionManager, SessionNotFoundError
from robotsix_browser.vault import (
    EntryNotFoundError,
    EntryOutOfScopeError,
    VaultClient,
    VaultError,
    VaultNotConfiguredError,
)


def get_manager(request: Request) -> SessionManager:
    manager: SessionManager = request.app.state.session_manager
    return manager


def get_filehub(request: Request) -> FileHubClient:
    client: FileHubClient = request.app.state.filehub_client
    return client


def get_vault(request: Request) -> VaultClient:
    client: VaultClient = request.app.state.vault_client
    return client


def _lookup(manager: SessionManager, session_id: str) -> Session:
    try:
        return manager.get(session_id)
    except SessionNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"unknown session {session_id!r}"
        ) from None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.session_manager = SessionManager(headless=settings.headless)
    app.state.filehub_client = FileHubClient(settings.file_hub_base_url)
    app.state.vault_client = VaultClient.from_settings(settings)
    try:
        yield
    finally:
        await app.state.session_manager.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="robotsix-browser",
        summary="Interactive headless-browser / form-filling service.",
        lifespan=lifespan,
    )
    app.state.settings = settings or get_settings()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/chat-skill")
    async def chat_skill_doc() -> dict[str, Any]:
        return chat_skill.chat_skill()

    @app.post("/sessions", response_model=SessionResponse)
    async def open_session(
        request: OpenSessionRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionResponse:
        session = await manager.open_session(request.session_id)
        return SessionResponse(session_id=session.id)

    @app.delete("/sessions/{session_id}")
    async def close_session(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> dict[str, str]:
        await manager.close_session(session_id)
        return {"status": "closed"}

    @app.post("/sessions/{session_id}/navigate", response_model=ActionResponse)
    async def navigate(
        session_id: str,
        request: NavigateRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        try:
            url = await operations.navigate(session.page, request)
        except UnsupportedUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ActionResponse(url=url)

    @app.get("/sessions/{session_id}/state", response_model=StateResponse)
    async def state(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> StateResponse:
        session = _lookup(manager, session_id)
        return await operations.get_state(session.page)

    @app.post("/sessions/{session_id}/click", response_model=ActionResponse)
    async def click(
        session_id: str,
        request: ClickRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        return ActionResponse(url=await operations.click(session.page, request))

    @app.post("/sessions/{session_id}/type", response_model=ActionResponse)
    async def type_text(
        session_id: str,
        request: TypeRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        return ActionResponse(url=await operations.type_text(session.page, request))

    @app.post("/sessions/{session_id}/select", response_model=ActionResponse)
    async def select(
        session_id: str,
        request: SelectRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        return ActionResponse(url=await operations.select_option(session.page, request))

    @app.post("/sessions/{session_id}/upload", response_model=ActionResponse)
    async def upload(
        session_id: str,
        request: UploadRequest,
        manager: SessionManager = Depends(get_manager),
        filehub: FileHubClient = Depends(get_filehub),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        try:
            url = await operations.upload(session.page, request, filehub)
        except InvalidFileIdError as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid file id: {exc}"
            ) from exc
        except FileHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ActionResponse(url=url)

    @app.post("/sessions/{session_id}/wait", response_model=ActionResponse)
    async def wait(
        session_id: str,
        request: WaitRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        session = _lookup(manager, session_id)
        return ActionResponse(url=await operations.wait(session.page, request))

    @app.get("/sessions/{session_id}/value", response_model=ValueResponse)
    async def value(
        session_id: str,
        selector: str = Query(..., description="CSS selector of the field to read"),
        manager: SessionManager = Depends(get_manager),
    ) -> ValueResponse:
        session = _lookup(manager, session_id)
        return ValueResponse(
            selector=selector,
            value=await operations.read_value(session.page, selector),
        )

    @app.post("/sessions/{session_id}/fill-credentials", response_model=ActionResponse)
    async def fill_credentials(
        session_id: str,
        request: FillCredentialsRequest,
        manager: SessionManager = Depends(get_manager),
        vault: VaultClient = Depends(get_vault),
    ) -> ActionResponse:
        """Inject a scoped Vaultwarden entry into the login form.

        Fetches the entry via the Bitwarden CLI and types username/password
        directly into the given fields.  The secret is never returned, logged,
        or surfaced to the agent.  This only fills the form; the human-gated
        ``/submit`` endpoint remains the only submit path.
        """
        session = _lookup(manager, session_id)
        try:
            url = await operations.fill_credentials(session.page, request, vault)
        except VaultNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except EntryOutOfScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except EntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except VaultError as exc:
            raise HTTPException(
                status_code=502, detail="credential retrieval failed"
            ) from exc
        return ActionResponse(url=url)

    @app.post("/sessions/{session_id}/submit", response_model=ActionResponse)
    async def submit(
        session_id: str,
        request: SubmitRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> ActionResponse:
        """HUMAN-GATED final submit / confirm; requires explicit operator OK."""
        session = _lookup(manager, session_id)
        return ActionResponse(url=await operations.submit(session.page, request))

    return app
