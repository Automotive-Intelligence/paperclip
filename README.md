# paperclip

## Production Foundation

This project now includes centralized runtime settings and startup validation in
`config/runtime.py`.

### Core Environment Controls

- `APP_ENV`: runtime environment label (default: `development`)
- `STRICT_STARTUP`: if `true`, startup fails when required runtime dependencies are missing
- `APP_TIMEZONE`: app timezone for metrics timestamps (default: `America/Chicago`)
- `APP_VERSION`: API version string reported by health/root endpoints
- `LOG_JSON`: structured JSON logs on/off (default: `true`)
- `LOG_LEVEL`: logger level (default: `INFO`)

### Runtime Checks

Startup now logs a single configuration summary and warning/fatal checks for:

- API auth configuration (`API_KEYS`)
- Postgres persistence (`DATABASE_URL`)
- LLM credentials for configured provider
- GHL CRM credentials

### Health Endpoints

- `GET /health`: liveness + runtime metadata
- `GET /health/ready`: readiness probe (returns `503` when not ready)

`/health/ready` is designed for deploy gates and can be wired into production monitoring.

### Service Reliability (Phase 2)

- External integrations now use retry-aware service calls in `services/http_client.py`
- Standardized service error envelope lives in `services/errors.py`
- GHL integration in `tools/ghl.py` now runs through this service layer
- Request correlation ID is emitted in logs and returned as `X-Request-ID`

### Validation

Run focused foundation tests:

`python3 -m unittest tests/test_phase2_foundation.py`