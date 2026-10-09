from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class TicketCreate(BaseModel):
    title: Title


class TicketUpdate(BaseModel):
    status: Literal["open", "closed"]


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    status: Literal["open", "closed"]
