"""
services/errors.py - Standardized service error envelope types.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

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