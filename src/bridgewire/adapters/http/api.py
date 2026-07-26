from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from bridgewire.adapters.http.models import (
    EventPageResponse,
    EventResponse,
    HealthResponse,
    StatusResponse,
)
from bridgewire.application.query_service import (
    InvalidQueryError,
    QueryUnavailableError,
    ReadOnlyQueryService,
)
from bridgewire.application.status_service import OperationalSnapshotUnavailableError
from bridgewire.audit import EventType, Severity


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    queries: ReadOnlyQueryService


def create_app(container: ApplicationContainer) -> FastAPI:
    """Create the read-only adapter without constructing hardware dependencies."""

    app = FastAPI(title="Bridgewire read-only API", version="1")

    @app.exception_handler(QueryUnavailableError)
    def query_unavailable(_request: Request, _exc: QueryUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "query service unavailable"})

    @app.exception_handler(OperationalSnapshotUnavailableError)
    def snapshot_unavailable(
        _request: Request, _exc: OperationalSnapshotUnavailableError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "operational status unavailable"})

    @app.exception_handler(InvalidQueryError)
    def invalid_query(_request: Request, exc: InvalidQueryError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> JSONResponse:
        result = container.queries.health()
        response = HealthResponse(
            status=result.status,
            controller_state=result.controller_state,
            reader_health=result.reader_health,
            operational_snapshot_stale=result.operational_snapshot_stale,
        )
        return JSONResponse(
            status_code=200 if result.healthy else 503,
            content=response.model_dump(mode="json"),
        )

    @app.get("/status")
    def status() -> StatusResponse:
        return StatusResponse.model_validate(container.queries.status().as_dict())

    @app.get("/events")
    def events(
        limit: Annotated[int | None, Query(ge=1)] = None,
        cursor: str | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> EventPageResponse:
        resolved_limit = (
            min(50, container.queries.maximum_event_page_size) if limit is None else limit
        )
        if resolved_limit > container.queries.maximum_event_page_size:
            raise HTTPException(
                status_code=422,
                detail=(f"limit must be between 1 and {container.queries.maximum_event_page_size}"),
            )
        page = container.queries.list_events(
            limit=resolved_limit,
            cursor=cursor,
            before=before,
            after=after,
            event_type=event_type,
            severity=severity,
        )
        return EventPageResponse(
            items=[EventResponse.model_validate(event.as_dict()) for event in page.items],
            next_cursor=page.next_cursor,
        )

    @app.get("/events/{event_id}")
    def event(event_id: UUID) -> EventResponse:
        result = container.queries.get_event(str(event_id))
        if result is None:
            raise HTTPException(status_code=404, detail="event not found")
        return EventResponse.model_validate(result.as_dict())

    return app
