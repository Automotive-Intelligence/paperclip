# paperclip

## Production Foundation

This project now includes centralized runtime settings and startup validation in
`config/runtime.py`.

### Core Environment Controls

- `APP_ENV`: runtime environment label (default: `development`)
- `STRICT_STARTUP`: if `true`, startup fails when required runtime dependencies are missing
- `APP_TIMEZONE`: app timezone for metrics timestamps (default: `America/Chicago`)
- `APP_VERSION`: API version string reported by health/root endpoints

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