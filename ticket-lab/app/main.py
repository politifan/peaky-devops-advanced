"""Run: uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18090."""
import logging
import json
import time
import uuid
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import database_url, make_engine
from app.models import Ticket
from app.schemas import TicketCreate, TicketRead, TicketUpdate

logger = logging.getLogger("ticket_lab")


def create_app(db_url: str | None = None) -> FastAPI:
    engine = make_engine(database_url(db_url))
    version = os.environ.get("APP_VERSION", "0.1.0")

    @asynccontextmanager
    async def lifespan(application):
        # No create_all here: missing migrations must remain visible.
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="Ticket Lab", version=version, lifespan=lifespan)


    registry = CollectorRegistry()
    readiness = Gauge('ticket_ready', 'Latest readiness probe result', registry=registry)
    readiness.set(0)
    requests_total = Counter('ticket_http_requests_total', 'Application HTTP requests',
                             ['method', 'route', 'status'], registry=registry)
    duration = Histogram('ticket_http_request_duration_seconds', 'Application request duration',
                         ['method', 'route', 'status'], buckets=(.005, .01, .025, .05, .1, .3, .5, 1, 2, 5, 10), registry=registry)

    @application.middleware('http')
    async def measure(request, call_next):
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['X-Request-ID'] = request_id
            return response
        finally:
            route = getattr(request.scope.get('route'), 'path', '__unmatched__')
            if route not in {'/health', '/ready', '/metrics'}:
                method = request.method if request.method in {'GET', 'POST', 'PATCH'} else 'OTHER'
                elapsed = time.monotonic() - started
                requests_total.labels(method, route, str(status)).inc()
                duration.labels(method, route, str(status)).observe(elapsed)
                logger.warning(json.dumps({'event': 'http_request', 'request_id': request_id,
                    'route': route, 'method': method, 'status': status, 'duration_ms': round(elapsed * 1000, 2)}))

    @application.get('/metrics')
    def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    def get_session():
        with Session(engine) as session:
            yield session

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, exception):
        # Do not disclose connection strings, credentials or SQL parameters.
        logger.warning("event=database_error type=%s", type(exception).__name__)
        return JSONResponse(status_code=503, content={"detail": "database_unavailable"})

    @application.get("/health")
    def health():
        return {"status": "alive", "version": version}

    @application.get("/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(select(Ticket.id, Ticket.title, Ticket.status).limit(1))
        except SQLAlchemyError as exception:
            readiness.set(0)
            logger.warning("event=readiness_failed type=%s", type(exception).__name__)
            raise HTTPException(status_code=503, detail="database_or_schema_unavailable") from None
        readiness.set(1)
        return {"status": "ready"}

    @application.post("/tickets", response_model=TicketRead, status_code=201)
    def create_ticket(data: TicketCreate, session: Session = Depends(get_session)):
        ticket = Ticket(title=data.title)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        return TicketRead.model_validate(ticket)

    @application.get("/tickets", response_model=list[TicketRead])
    def list_tickets(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                     session: Session = Depends(get_session)):
        rows = session.scalars(select(Ticket).order_by(Ticket.id).offset(offset).limit(limit))
        return [TicketRead.model_validate(row) for row in rows]

    @application.get("/tickets/{ticket_id}", response_model=TicketRead)
    def get_ticket(ticket_id: int, session: Session = Depends(get_session)):
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return TicketRead.model_validate(ticket)

    @application.patch("/tickets/{ticket_id}", response_model=TicketRead)
    def update_ticket(ticket_id: int, data: TicketUpdate, session: Session = Depends(get_session)):
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        ticket.status = data.status
        session.commit()
        session.refresh(ticket)
        return TicketRead.model_validate(ticket)

    return application
