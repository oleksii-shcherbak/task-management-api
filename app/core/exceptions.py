"""Application exception hierarchy.

All domain errors subclass AppException, which carries a machine-readable
`code` alongside the HTTP status and human message.  Raise a subclass
rather than HTTPException directly so that the global exception handler can
emit a consistent `{"error": {"code": ..., "message": ...}}` envelope.
"""

from fastapi import HTTPException


class AppException(HTTPException):
    """
    Base class for all application exceptions.
    Carries a machine-readable 'code' alongside the human message.
    Subclass this for every error type — never raise HTTPException directly.
    """

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(status_code=self.status_code, detail=message)

    def __init_subclass__(
        cls, status_code: int = 500, code: str = "INTERNAL_ERROR", **kwargs: object
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.status_code = status_code
        cls.code = code


class NotFoundError(AppException, status_code=404, code="NOT_FOUND"):
    pass


class UnauthorizedError(AppException, status_code=401, code="UNAUTHORIZED"):
    pass


class ForbiddenError(AppException, status_code=403, code="FORBIDDEN"):
    pass


class ConflictError(AppException, status_code=409, code="CONFLICT"):
    pass


class ValidationError(AppException, status_code=422, code="VALIDATION_ERROR"):
    pass


class RateLimitError(AppException, status_code=429, code="RATE_LIMIT_EXCEEDED"):
    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after
