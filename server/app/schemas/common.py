from pydantic import BaseModel


class MessageResponse(BaseModel):
    """A bare confirmation, e.g. after a delete."""

    message: str
