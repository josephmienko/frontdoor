from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from bridgewire.adapters.http.models import (
    EventPageResponse,
    EventResponse,
    HealthResponse,
    StatusResponse,
)
from bridgewire.application.query_service import ReadOnlyQueryService
from bridgewire.audit import EventType, Severity


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    queries: ReadOnlyQueryService


def create_app(container: ApplicationContainer) -> FastAPI:
    """Create the read-only adapter without constructing hardware dependencies."""

    app = FastAPI(title="Bridgewire read-only API", version="1")

    @app.get("/health")
    def health() -> JSONResponse:
        result = container.queries.health()
        response = HealthResponse(
            status=result.status,
            controller_state=result.controller_state,
            reader_health=result.reader_health,
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
        limit: Annotated[int, Query(ge=1)] = 50,
        before: datetime | None = None,
        after: datetime | None = None,
        event_type: EventType | None = None,
        severity: Severity | None = None,
    ) -> EventPageResponse:
        if limit > container.queries.maximum_event_page_size:
            raise HTTPException(
                status_code=422,
                detail=(f"limit must be between 1 and {container.queries.maximum_event_page_size}"),
            )
        page = container.queries.list_events(
            limit=limit,
            before=before,
            after=after,
            event_type=event_type,
            severity=severity,
        )
        return EventPageResponse(
            events=[EventResponse.model_validate(event.as_dict()) for event in page]
        )

    @app.get("/events/{event_id}")
    def event(event_id: str) -> EventResponse:
        result = container.queries.get_event(event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="event not found")
        return EventResponse.model_validate(result.as_dict())

    return app
