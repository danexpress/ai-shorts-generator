class ApiError(Exception):
    """Creator-safe API error; do not put worker diagnostics in the message."""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
