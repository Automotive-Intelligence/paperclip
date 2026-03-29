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

### Multi-CRM Plug And Play

Paperclip supports per-company CRM routing with optional per-agent override.

- `BUSINESS_CRM_MAP` (JSON map): default business to CRM provider mapping
- `AGENT_CRM_MAP` (JSON map): optional agent to CRM provider overrides

Default mapping:

- `aiphoneguy` -> `ghl`
- `callingdigital` -> `attio`
- `autointelligence` -> `hubspot`

Provider credentials:

- GHL: `GHL_API_KEY`, `GHL_LOCATION_ID`
- HubSpot: `HUBSPOT_API_KEY` (or `HUBSPOT_ACCESS_TOKEN`)
- Attio: `ATTIO_API_KEY`

Useful endpoint:

- `GET /api/crm/config` returns active mapping and provider readiness.

### GHL Site Publishing (AI Phone Guy)

Paperclip can publish queued AI Phone Guy content to a GoHighLevel site workflow
through a secure webhook adapter.

Required env vars:

- `GHL_SITE_PUBLISH_WEBHOOK_URL`: inbound GHL workflow/webhook URL that creates or updates a site post

Optional env vars:

- `GHL_SITE_PUBLISH_WEBHOOK_AUTH`: custom Authorization header value sent to the publish webhook

Operational endpoint:

- `POST /content/publish/ghl?limit=5`

Behavior:

- Pulls queued content for `aiphoneguy`
- Builds a branded SVG hero graphic payload for each item
- Sends title/body/slug/graphic payload to your GHL webhook
- Marks successful items as published in `content_queue`

### Ghost Publishing (Calling Digital)

Paperclip can publish queued Calling Digital long-form content directly to Ghost
through the Ghost Admin API.

Required env vars for Calling Digital:

- `CALLINGDIGITAL_GHOST_API_URL`: Ghost site base URL, e.g. `https://blog.calling.digital`
- `CALLINGDIGITAL_GHOST_ADMIN_API_KEY`: Ghost Admin API key in `<id>:<secret>` format

Operational endpoint:

- `POST /content/publish/ghost/callingdigital?limit=5`

Behavior:

- Pulls queued content for `callingdigital`
- Filters out social-only items and publishes blog/site items to Ghost
- Marks successful items as published in `content_queue`
- Designed to scale to future Ghost-backed businesses via `{BUSINESS}_GHOST_*` env vars

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

### Task Master Watchdog

Paperclip now includes a Task Master watchdog that runs on an interval and checks
whether execution is keeping pace while you are in active selling windows.

Default behavior:

- Runs every 5 minutes
- Flags stale agents (default stale threshold: 180 minutes)
- Flags CRM/email readiness blockers (missing keys/scopes)
- Flags approval queue pressure and paused scheduler jobs
- Persists checks in Postgres (`taskmaster_checks`) when available

Task Master env controls:

- `TASKMASTER_ENABLED` (default: `true`)
- `TASKMASTER_INTERVAL_MINUTES` (default: `5`)
- `TASKMASTER_STALE_AGENT_MINUTES` (default: `180`)
- `TASKMASTER_WORK_START_HOUR` (default: `8`)
- `TASKMASTER_WORK_END_HOUR` (default: `20`)
- `TASKMASTER_WEEKDAYS_ONLY` (default: `true`)

Task Master alert controls:

- `TASKMASTER_ALERTS_ENABLED` (default: `false`)
- `TASKMASTER_ALERT_ON_AMBER` (default: `false`)
- `TASKMASTER_ALERT_COOLDOWN_MINUTES` (default: `30`)
- `TASKMASTER_ALERT_WEBHOOK_URL` (required for outbound alert delivery)
- `TASKMASTER_ALERT_WEBHOOK_AUTH` (optional Authorization header value)

Task Master endpoints:

- `GET /api/taskmaster/status` (optional `?run_check=true`)
- `GET /api/taskmaster/history?limit=25`
- `POST /api/taskmaster/run` (authenticated)
- `POST /api/taskmaster/alert/test` (authenticated, bypasses cooldown)

### Service Reliability (Phase 2)

- External integrations now use retry-aware service calls in `services/http_client.py`
- Standardized service error envelope lives in `services/errors.py`
- GHL integration in `tools/ghl.py` now runs through this service layer
- Request correlation ID is emitted in logs and returned as `X-Request-ID`

### Validation

Run focused foundation tests:

`python3 -m unittest tests/test_phase2_foundation.py`