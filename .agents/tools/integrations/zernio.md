# Zernio
Social media scheduling platform supporting 14+ networks via unified REST API.

## Capabilities

| Integration | Available | Notes |
|-------------|-----------|-------|
| API | ✓ | REST API v1 for profiles, accounts, posts, analytics |
| MCP | - | Not available yet |
| CLI | - | Zernio has a CLI but not bundled in Paperclip |
| SDK | ✓ | [Python SDK + 7 other languages](https://docs.zernio.com/resources/sdks) |

## Authentication

- **Type**: Bearer Token
- **Header**: `Authorization: Bearer {api_key}`
- **Env var**: `ZERNIO_API_KEY`
- **Get key**: 
  1. Log into [zernio.com](https://zernio.com/)
  2. Go to Settings → API Keys
  3. Click Create API Key (shown once, copy immediately)
- **Format**: `sk_` prefix + 64 hex characters (67 total)

## Supported Platforms

| Platform | Zernio ID | Scheduling | Media | Notes |
|----------|-----------|-----------|-------|-------|
| Twitter/X | twitter | ✓ | ✓ | 280 char limit |
| Instagram | instagram | ✓ | ✓ | 2200 char limit |
| Facebook | facebook | ✓ | ✓ | 63206 char limit |
| LinkedIn | linkedin | ✓ | ✓ | 3000 char limit |
| TikTok | tiktok | ✓ | ✓ (video only) | 2200 char limit |
| YouTube | youtube | ✓ | ✓ (video only) | No char limit |
| Pinterest | pinterest | ✓ | ✓ | 500 char limit |
| Reddit | reddit | - | ✓ | Can't schedule |
| Bluesky | bluesky | - | ✓ | Can't schedule |
| Threads | threads | - | ✓ | Can't schedule |
| Google Business | googlebusiness | ✓ | ✓ | 300 char limit |
| Telegram | telegram | - | ✓ | 4096 char limit |
| Snapchat | snapchat | - | ✓ | Can't schedule |
| WhatsApp | whatsapp | - | ✓ | Can't schedule |

## Key Concepts

- **Profiles** - Containers grouping social accounts (brands/projects). Each business gets its own profile.
- **Accounts** - Connected social media accounts (e.g., "The AI Phone Guy Twitter"). Auth required to connect.
- **Posts** - Content to publish, schedulable across multiple platforms simultaneously.
- **Queue** - Optional recurring time slots for auto-scheduling posts.

## Paperclip Integration

### Setup Checklist

1. **Get API Key**
   ```bash
   # 1. Create Zernio account at https://zernio.com/
   # 2. Settings → API Keys → Create API Key
   # 3. Copy the key
   ```

2. **Set Environment Variable**
   ```bash
   # Railway
   ZERNIO_API_KEY=sk_xxxxx...
   
   # Or locally (.env)
   ZERNIO_API_KEY=sk_xxxxx...
   ```

3. **Create Business Profiles** (in Zernio dashboard)
   ```
   Profile: "The AI Phone Guy"
   Profile: "Calling Digital"
   Profile: "Automotive Intelligence"
   ```

4. **Connect Social Accounts per Profile**
   - For each profile, click "Connect Account"
   - Select platform (Twitter, LinkedIn, Instagram, etc.)
   - Authorize OAuth flow
   - Repeat for all desired platforms

5. **Verify Setup**
   ```python
   from tools.zernio import zernio_ready, get_zernio_profiles, list_zernio_accounts
   
   if zernio_ready():
       profiles = get_zernio_profiles()
       for p in profiles:
           accounts = list_zernio_accounts(p["_id"])
           print(f"{p['name']}: {len(accounts)} accounts")
   ```

### Publishing Content

#### Via API Endpoint

```bash
# Publish up to 5 queued AI Phone Guy content pieces to Zernio
curl -X POST \
  http://localhost:8000/content/publish/zernio/aiphoneguy?limit=5 \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Via Python

```python
from tools.zernio import publish_to_zernio, list_zernio_accounts

# Get a profile's accounts
accounts = list_zernio_accounts(profile_id="prof_xyz123")

# Publish to Twitter and LinkedIn
result = publish_to_zernio(
    content="Just launched AI Phone Guy! 🎉",
    platforms=["twitter", "linkedin"],
    account_ids=[acc["_id"] for acc in accounts],
    publish_now=True,
)
print(result["_id"])  # Zernio post ID
```

#### Schedule for Later

```python
result = publish_to_zernio(
    content="Check out our latest guide...",
    platforms=["linkedin"],
    account_ids=["acc_xyz"],
    scheduled_for="2026-04-15T09:00:00",
    timezone="America/Chicago",
)
```

### Content Agent Integration

Zoe, Sofia, and Chase agents already parse content into Zernio-compatible format:

```python
# From their task output, pieces look like:
{
    "platform": "twitter",  # or instagram, linkedin, tiktok, etc.
    "content": "...",       # Post text
    "media_url": "...",     # Optional image/video URL
    "scheduled_for": "2026-04-15T09:00:00",  # Optional
    "title": "...",         # For tracking
}
```

Content flows: **Agent → parse_content_pieces() → queue_content() → /content/publish/zernio/{business_key} → Zernio**

### Analytics

```python
from tools.zernio import get_zernio_analytics

# Get post-level analytics
analytics = get_zernio_analytics(post_id="post_xyz123")

# Get profile analytics over date range
analytics = get_zernio_analytics(
    profile_id="prof_xyz123",
    start_date="2026-03-01",
    end_date="2026-03-31",
)

# Results include impressions, engagement, reach across platforms
```

### Revenue Tracker Integration

Content publishing events are automatically tracked:

```python
# Tracked events
track_event(
    "content_published_social",
    business_key="aiphoneguy",
    agent_name="zoe",
    metadata={
        "content_id": "...",
        "provider": "zernio",
        "platform": "twitter",
        "zernio_post_id": "post_xyz",
    }
)
```

## Common Operations (Python)

### Create a Profile
```python
from tools.zernio import create_zernio_profile

profile = create_zernio_profile(
    name="The AI Phone Guy",
    description="AI receptionist marketing for SMBs"
)
print(f"Profile ID: {profile['_id']}")
```

### Connect an Account
```python
from tools.zernio import get_zernio_connect_url

auth_url = get_zernio_connect_url(
    profile_id="prof_xyz123",
    platform="twitter"
)
# Open URL in browser to authorize
```

### List Connected Accounts
```python
from tools.zernio import list_zernio_accounts

accounts = list_zernio_accounts(profile_id="prof_xyz123")
for acc in accounts:
    print(f"{acc['platform']}: {acc.get('username', 'N/A')}")
```

### Create a Draft
```python
from tools.zernio import create_zernio_draft

draft = create_zernio_draft(
    content="This is a draft post",
    platforms=["twitter", "linkedin"],
)
print(f"Draft ID: {draft['_id']}")
# Edit on Zernio dashboard before publishing
```

### Delete a Post
```python
from tools.zernio import delete_zernio_post

delete_zernio_post(post_id="post_xyz123")
```

## Rate Limits

- **Standard plans**: ~200 requests/minute (varies by plan)
- **Bulk endpoints**: Batching recommended
- **Auth tokens**: Expire after ~12 hours (SDK refreshes automatically)
- See [Zernio rate limits docs](https://docs.zernio.com/api-limits) for your plan

## When to Use

- **Unified social posting** across 14+ platforms from single API
- **Eliminating GHL Social Planner** dependency (single integration point)
- **Cross-platform scheduling** for content agents (Zoe, Sofia, Chase)
- **Customer white-label** offering (resell scheduling service)
- **Multi-brand management** (profiles group accounts by business)

## Troubleshooting

### "Zernio API key not configured"
- Set `ZERNIO_API_KEY` in Railway or .env
- Verify key starts with `sk_` and is 67 characters
- Keys are shown once at creation; regenerate if lost

### "No accounts found for platforms"
- Go to Zernio dashboard → select profile → connect accounts
- Each platform requires OAuth authorization
- After connecting, `list_zernio_accounts()` should return them

### Posts not scheduling
- Check platform supports scheduling (Reddit, Bluesky, Threads don't)
- Use `publish_now=True` for platforms that don't support scheduling
- Verify `scheduled_for` is ISO 8601 format: `YYYY-MM-DDTHH:MM:SS`

### Rate limit errors
- Reduce batch size (max 25 posts per request via API endpoint)
- Implement exponential backoff retry logic
- Contact Zernio support for higher limits

## Relevant Skills

- social-media-manager
- social-content
- brand-guidelines (cross-platform voice)
- analytics-tracking (post-level analytics)

## Related Tools

- **GHL Social Planner** - Legacy integration, being replaced by Zernio for broader platform support
- **Ghost** - Blog publishing (separate from social)
- **Buffer** - Alternative scheduling tool (not currently integrated)

## Links

- [Zernio Docs](https://docs.zernio.com/)
- [API Reference](https://docs.zernio.com/api/openapi)
- [Zernio Dashboard](https://zernio.com/)
- [Pricing](https://docs.zernio.com/pricing)
