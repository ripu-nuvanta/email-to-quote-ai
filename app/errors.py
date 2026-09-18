class NotFound(Exception):
    pass


class Conflict(Exception):
    """The request is valid but not allowed in the current state (e.g. approving a sent quote)."""

    def __init__(self, message: str, lines: list[int] | None = None):
        super().__init__(message)
        self.lines = lines or []
