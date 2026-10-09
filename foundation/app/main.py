"""Run: uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18090."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
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
            logger.warning("event=readiness_failed type=%s", type(exception).__name__)
            raise HTTPException(status_code=503, detail="database_or_schema_unavailable") from None
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
