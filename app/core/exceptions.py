from fastapi import HTTPException


class AppException(HTTPException):
    """
    Base class for all application exceptions.
    Carries a machine-readable 'code' alongside the human message.
    Subclass this for every error type — never raise HTTPException directly.
    """

    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str):
        super().__init__(status_code=self.status_code, detail=message)

    def __init_subclass__(
        cls, status_code: int = 500, code: str = "INTERNAL_ERROR", **kwargs
    ):
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
