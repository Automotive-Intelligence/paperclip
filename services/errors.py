"""
services/errors.py - Standardized service error envelope types.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ServiceError:
    provider: str
    operation: str
    message: str
    status_code: Optional[int] = None
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "operation": self.operation,
            "message": self.message,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "details": self.details or {},
        }


class ServiceCallError(RuntimeError):
    def __init__(self, error: ServiceError):
        self.error = error
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        code = self.error.status_code if self.error.status_code is not None else "n/a"
        return (
            f"{self.error.provider}.{self.error.operation} failed "
            f"(status={code}, retryable={self.error.retryable}): {self.error.message}"
        )