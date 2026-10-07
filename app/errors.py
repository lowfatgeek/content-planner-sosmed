"""Error domain → dipetakan ke HTTP status di router (PRD D23/§7b)."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, *, code: str | None = None, details: list | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details: list = list(details or [])

    def to_dict(self) -> dict:
        body: dict = {"error": self.code, "message": self.message}
        if self.details:
            body["detail"] = self.details
        return body


class ValidationError(AppError):
    status_code = 422
    code = "validasi"


class NotFound(AppError):
    status_code = 404
    code = "tidak_ditemukan"


class Forbidden(AppError):
    status_code = 403
    code = "terlarang"


class Unauthorized(AppError):
    status_code = 401
    code = "tidak_terautentikasi"


class Conflict(AppError):
    status_code = 409
    code = "konflik"


class BadRequest(AppError):
    status_code = 400
    code = "permintaan_buruk"


class TransitionError(ValidationError):
    code = "transisi_tidak_sah"
