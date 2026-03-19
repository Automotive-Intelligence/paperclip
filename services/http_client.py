"""
services/http_client.py - Retrying HTTP wrapper for external service calls.
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

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from services.errors import ServiceError


@dataclass
class ServiceResponse:
    ok: bool
    status_code: Optional[int]
    data: Optional[Dict[str, Any]]
    error: Optional[ServiceError]


def _safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        body = response.json()
        if isinstance(body, dict):
            return body
        return {"raw": body}
    except Exception:
        return {"text": (response.text or "")[:1000]}


def _is_retryable_status(status_code: int) -> bool:
    return status_code in (408, 409, 425, 429, 500, 502, 503, 504)


def request_with_retry(
    provider: str,
    operation: str,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
    max_attempts: int = 3,
    backoff_seconds: float = 0.6,
) -> ServiceResponse:
    """Execute HTTP call with bounded retries and envelope output."""
    last_error: Optional[ServiceError] = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout,
            )

            body = _safe_json(resp)
            if 200 <= resp.status_code < 300:
                return ServiceResponse(ok=True, status_code=resp.status_code, data=body, error=None)

            retryable = _is_retryable_status(resp.status_code)
            last_error = ServiceError(
                provider=provider,
                operation=operation,
                message=f"HTTP {resp.status_code}",
                status_code=resp.status_code,
                retryable=retryable,
                details=body,
            )

            if retryable and attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
                continue
            return ServiceResponse(ok=False, status_code=resp.status_code, data=body, error=last_error)

        except requests.RequestException as exc:
            last_error = ServiceError(
                provider=provider,
                operation=operation,
                message=str(exc),
                status_code=None,
                retryable=True,
                details={"exception": exc.__class__.__name__},
            )
            if attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
                continue
            return ServiceResponse(ok=False, status_code=None, data=None, error=last_error)

        except Exception as exc:
            last_error = ServiceError(
                provider=provider,
                operation=operation,
                message=str(exc),
                status_code=None,
                retryable=False,
                details={"exception": exc.__class__.__name__},
            )
            return ServiceResponse(ok=False, status_code=None, data=None, error=last_error)

    # Unreachable safety fallback
    if last_error is None:
        last_error = ServiceError(
            provider=provider,
            operation=operation,
            message="Unknown service failure",
            retryable=False,
            details={"internal": "no_attempts_executed"},
        )
    return ServiceResponse(ok=False, status_code=last_error.status_code, data=None, error=last_error)