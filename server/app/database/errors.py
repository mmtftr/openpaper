"""Domain errors the data layer raises for cases callers must tell apart.

Anything else a CRUD call hits (a lost connection, a constraint violation)
propagates as the SQLAlchemy exception it is; the request's session is
rolled back when `get_db` closes it. The API maps these to HTTP responses
in one place (`app.api.errors.install_error_handlers`).
"""


class NotFound(LookupError):
    """The row doesn't exist or isn't the owner's (the API answers 404).

    `detail` is the message the client sees.
    """

    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(detail)
        self.detail = detail
