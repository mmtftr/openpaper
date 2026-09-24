"""Datetime field types that keep the API's existing wire formats.

Pydantic writes a UTC datetime as `2024-01-01T00:00:00Z`; the handlers
predate the typed models and emit `datetime.isoformat()`
(`2024-01-01T00:00:00+00:00`) or `str(datetime)`
(`2024-01-01 00:00:00+00:00`). These annotated types keep those exact
strings in JSON while the model fields stay real datetimes.
"""

from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

_DATETIME_SCHEMA = WithJsonSchema({"type": "string", "format": "date-time"})

# `datetime.isoformat()` on the wire.
IsoDatetime = Annotated[
    datetime,
    PlainSerializer(lambda v: v.isoformat(), return_type=str, when_used="json"),
    _DATETIME_SCHEMA,
]

# `str(datetime)` on the wire (space instead of `T`), as the paper list
# endpoints have always produced.
StrDatetime = Annotated[
    datetime,
    PlainSerializer(str, return_type=str, when_used="json"),
    _DATETIME_SCHEMA,
]
