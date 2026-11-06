"""REST endpoints. The chat payload carries only user-facing fields; tool calls, models and
tokens are served separately by the debug trace endpoint."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_service, get_settings
from app.api.errors import ApiError
from app.config import Settings
from app.db import conversations as repo
from app.schemas.conversation import (
    ConversationRequest,
    ConversationResponse,
    MessageOut,
    SessionSummary,
    TraceOut,
)
from app.services.conversation import ConversationService

router = APIRouter()
# Mounted only with DEBUG_API=true: otherwise the route does not exist (404, absent from /docs).
debug_router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/api/conversation", response_model=ConversationResponse)
async def conversation(
    body: ConversationRequest,
    service: Annotated[ConversationService, Depends(get_service)],
    settings: CurrentSettings,
) -> ConversationResponse:
    query = body.query.strip()
    if not query:
        raise ApiError(400, "empty_query", "The query is empty.")
    if len(query) > settings.max_query_chars:
        limit = settings.max_query_chars
        raise ApiError(400, "query_too_long", f"The query is longer than {limit} characters.")
    return await service.handle(body.session_id, query)


@router.get("/api/sessions", response_model=list[SessionSummary])
async def list_sessions(db: Db) -> list[SessionSummary]:
    return [
        SessionSummary(
            id=r.session.id,
            title=r.session.title,
            created_at=r.session.created_at,
            updated_at=r.session.updated_at,
            message_count=r.message_count,
        )
        for r in await repo.list_sessions(db)
    ]


@router.get("/api/sessions/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: uuid.UUID, db: Db) -> list[MessageOut]:
    if await repo.get_session(db, session_id) is None:
        raise ApiError(404, "session_not_found", "Conversation not found.")
    out = []
    for m in await repo.list_messages(db, session_id):
        p = m.payload or {}  # internal_error, if any, is deliberately not copied
        out.append(
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                status=m.status,
                created_at=m.created_at,
                response_type=p.get("response_type"),
                products=p.get("products", []),
                highlight_fields=p.get("highlight_fields", []),
                clarification=p.get("clarification"),
                suggestions=p.get("suggestions", []),
                warnings=p.get("warnings", []),
            )
        )
    return out


@router.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: uuid.UUID, db: Db) -> Response:
    if not await repo.delete_session(db, session_id):
        raise ApiError(404, "session_not_found", "Conversation not found.")
    await db.commit()
    return Response(status_code=204)


@debug_router.get("/api/messages/{message_id}/trace", response_model=TraceOut)
async def message_trace(message_id: uuid.UUID, db: Db) -> TraceOut:
    trace = await repo.get_trace(db, message_id)
    if trace is None:
        raise ApiError(404, "message_not_found", "Message not found.")
    return TraceOut(
        message_id=trace.message.id,
        session_id=trace.message.session_id,
        tool_calls=trace.tool_calls,
        usage=trace.usage,
        payload=trace.message.payload or {},
    )


@router.get("/health")
async def health(db: Db, settings: CurrentSettings) -> JSONResponse:
    """Database status and the configured provider and models (never keys). A status report,
    not an error: it keeps this body with 503 when the database is down."""
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    body = {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "llm_provider": settings.llm_provider,
        "agent_model": settings.agent_model,
        "fallback_model": settings.fallback_model,
    }
    return JSONResponse(status_code=200 if database == "ok" else 503, content=body)
