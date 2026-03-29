# Zernio Deployment Checklist

**Integration Status**: ✅ DEVELOPMENT COMPLETE  
**Ready for Configuration**: Yes  
**Last Updated**: March 28, 2026

---

## Phase 1: Code & Environment Setup ✅

- [x] Created `tools/zernio.py` — Production-grade Zernio API wrapper
  - Profile management (create, list, get)
  - Account management (list, connect, disconnect)
  - Post publishing (draft, schedule, publish now)
  - Analytics integration
  - Content agent helpers
  - Supports 14+ platforms

- [x] Updated `requirements.txt` — Added `zernio>=0.1.0`

- [x] Integrated into `app.py`
  - Zernio imports added to tool imports
  - Startup checks log Zernio readiness
  - Profile/account discovery in lifespan startup
  - New POST endpoint: `/content/publish/zernio/{business_key}`

- [x] Created integration documentation
  - `.agents/tools/integrations/zernio.md` — Full setup guide
  - Added Zernio to `.agents/tools/REGISTRY.md`

- [x] Created verification script
  - `verify_zernio_setup.py` — Validate Zernio configuration

---

## Phase 2: Configuration (Before Launch) 🔄 IN PROGRESS

### 2.1 Get Zernio API Key
- [ ] Visit https://zernio.com/ and sign up (if needed)
- [ ] Go to Settings → API Keys
- [ ] Click "Create API Key"
- [ ] Copy key immediately (shown only once)
- [ ] Store securely in Railway secrets manager

### 2.2 Set Environment Variable
```bash
# Railway dashboard → Environment Variables
ZERNIO_API_KEY=sk_xxxxx...

# Or locally (.env)
ZERNIO_API_KEY=sk_xxxxx...
```
- [ ] Set in Railway staging environment
- [ ] Verify with `verify_zernio_setup.py`

### 2.3 Create Business Profiles (Zernio Dashboard)
For each business in Paperclip, create a profile:

#### The AI Phone Guy
- [ ] Profile name: "The AI Phone Guy"
- [ ] Description: "AI receptionist marketing for local service businesses"
- [ ] Note profile ID: `prof_xxxxx`

#### Calling Digital
- [ ] Profile name: "Calling Digital"
- [ ] Description: "Digital marketing agency for SMBs"
- [ ] Note profile ID: `prof_xxxxx`

#### Automotive Intelligence
- [ ] Profile name: "Automotive Intelligence"
- [ ] Description: "AI-powered intelligence for auto dealers"
- [ ] Note profile ID: `prof_xxxxx`

### 2.4 Connect Social Accounts per Profile
For **The AI Phone Guy** profile, connect:
- [ ] Twitter/X (warm B2B-to-SMB tone)
- [ ] LinkedIn (thought leadership)
- [ ] Instagram (visual marketing)
- [ ] TikTok (short-form content)
- [ ] Facebook (reach)
- [ ] Google Business (local)

For **Calling Digital** profile:
- [ ] LinkedIn (agency positioning)
- [ ] Twitter/X (industry updates)
- [ ] Instagram (portfolio)
- [ ] Google Business (local)

For **Automotive Intelligence** profile:
- [ ] Facebook (dealer audience)
- [ ] Instagram (visual inventory)
- [ ] YouTube (educational content)
- [ ] TikTok (viral dealer updates)
- [ ] Google Business (dealership info)

### 2.5 Verify Setup Locally
```bash
cd /Users/michaelrodriguez/Documents/GitHub/paperclip

# Run verification script
python3 verify_zernio_setup.py

# Expected output:
# ✓ ZERNIO_API_KEY is set
# ✓ tools.zernio imported successfully
# ✓ Zernio is configured and ready
# ✓ Found 3 profile(s)
# ✓ Found Zernio publishing endpoint
```

---

## Phase 3: Integration Testing 🔄 READY

### 3.1 Endpoint Testing
```bash
# Test Zernio publishing endpoint (no content queued yet)
curl -X POST "http://localhost:8000/content/publish/zernio/aiphoneguy?limit=5" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json"

# Expected: 
# {
#   "status": "ok",
#   "published": 0,
#   "failed": 0,
#   "results": [],
#   "message": "No queued aiphoneguy social content found."
# }
```

- [ ] `/content/publish/zernio/aiphoneguy` — Returns 200 OK
- [ ] `/content/publish/zernio/callingdigital` — Returns 200 OK
- [ ] `/content/publish/zernio/autointelligence` — Returns 200 OK

### 3.2 Content Pipeline Integration
- [ ] Zoe agent (AI Phone Guy) generates social content
  - Content is queued in `content_queue` table
  - `parse_content_pieces()` extracts: platform, content, media_url, scheduled_for
- [ ] Call `/content/publish/zernio/aiphoneguy?limit=1`
  - Picks first queued piece
  - Publishes to Zernio
  - Returns: `{"status": "published", ...}`
- [ ] Verify post appears in Zernio dashboard
- [ ] Check revenue tracker captured event: `content_published_social`

### 3.3 Multi-Platform Publishing
- [ ] Create content piece with `platform: "twitter"`
  - Publish via `/content/publish/zernio/aiphoneguy`
  - Verify tweet appears on connected Twitter account
- [ ] Create content piece with `platform: "linkedin"`
  - Publish via `/content/publish/zernio/aiphoneguy`
  - Verify post appears on connected LinkedIn account
- [ ] *Repeat for Instagram, TikTok, Facebook*

### 3.4 Scheduling Test
- [ ] Create content piece with future `scheduled_for` time
  - Format: ISO 8601 `"2026-04-01T14:00:00"`
- [ ] Publish via `/content/publish/zernio/aiphoneguy`
- [ ] Verify post shows as "Scheduled" in Zernio dashboard
- [ ] Verify post publishes at scheduled time

### 3.5 Analytics Test
- [ ] Publish a test post via Zernio
- [ ] Wait 5+ minutes for metrics to populate
- [ ] Check `/revenue` endpoint for `content_published_social` events
- [ ] Verify metadata includes: `provider: "zernio"`, `platform`, `zernio_post_id`

---

## Phase 4: Production Deployment 🔄 READY

### 4.1 Pre-Launch Checklist
- [ ] ZERNIO_API_KEY set in Railway production environment
- [ ] All 3 business profiles created and verified in Zernio
- [ ] All social accounts connected and tested
- [ ] `/content/publish/zernio/{business_key}` endpoint tested
- [ ] Content agents have generated sample content
- [ ] Publishing endpoint tested end-to-end
- [ ] Error handling verified (rate limits, auth failures, network issues)

### 4.2 Deploy to Production
- [ ] Merge changes to main branch
- [ ] Deploy via Railway (auto-deploys from main)
- [ ] Verify startup logs mention Zernio profiles:
  ```
  [Zernio] Initialized with 3 profile(s)
  [Zernio] Profile 'The AI Phone Guy': 6 account(s)
  [Zernio] Profile 'Calling Digital': 4 account(s)
  [Zernio] Profile 'Automotive Intelligence': 5 account(s)
  ```
- [ ] Run `verify_zernio_setup.py` in production environment

### 4.3 Customer-Facing Features (v1)
- [ ] Zernio publishing is internal-use only (agents to Zernio)
- [ ] No customer-facing pricing or white-label yet
- [ ] Next phase: Expose Zernio as customer add-on

---

## Phase 5: Ongoing Operations

### 5.1 Monitoring
- [ ] Monitor Zernio API rate limits (logs warnings at ~80% usage)
- [ ] Track failed publishes via `/revenue` dashboard
- [ ] Check Zernio dashboard for account disconnections
- [ ] Monitor scheduling accuracy (posts publish at intended times)

### 5.2 Maintenance
- [ ] Rotate ZERNIO_API_KEY periodically
- [ ] Review and update platform capabilities if Zernio adds new platforms
- [ ] Update polling if API rate limits change
- [ ] Document any new platform-specific behaviors

### 5.3 Scaling Considerations
- [ ] If publishing > 100 posts/day, consider batch API endpoints
- [ ] Implement post-publish webhook for real-time analytics
- [ ] Consider caching profile/account list to reduce API calls
- [ ] Add Zernio queue/recurring slots for consistent posting schedule

---

## Files Created/Modified

### New Files
- `tools/zernio.py` — Production API wrapper (420 lines)
- `.agents/tools/integrations/zernio.md` — Integration guide (350 lines)
- `verify_zernio_setup.py` — Verification script (120 lines)

### Modified Files
- `requirements.txt` — Added `zernio>=0.1.0`
- `app.py` — Added imports, startup checks, publishing endpoint
- `.agents/tools/REGISTRY.md` — Added Zernio to tool registry

### Documentation
- Session memory: `/memories/session/zernio-setup.md`
- This checklist: `ZERNIO_DEPLOYMENT_CHECKLIST.md`

---

## API Reference

### Environment Variable
```
ZERNIO_API_KEY=sk_xxxx... (67 characters)
```

### Publishing Endpoint
```
POST /content/publish/zernio/{business_key}?limit=5

Path Parameters:
  business_key: "aiphoneguy" | "callingdigital" | "autointelligence"

Query Parameters:
  limit: 1-25 (default: 5)

Headers:
  Authorization: Bearer {YOUR_API_KEY}

Response:
{
  "status": "ok",
  "published": 2,
  "failed": 0,
  "results": [
    {
      "content_id": "...",
      "title": "...",
      "platform": "twitter",
      "status": "published",
      "zernio_post_id": "post_xyz123"
    }
  ]
}
```

### Supported Platforms
`twitter`, `x`, `instagram`, `facebook`, `linkedin`, `tiktok`, `youtube`, `pinterest`, `reddit`, `bluesky`, `threads`, `googlebusiness`, `telegram`, `snapchat`, `whatsapp`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "ZERNIO_API_KEY not configured" | Set env var, restart app |
| "No profiles found" | Create profiles in Zernio dashboard |
| "No accounts found for platforms" | Connect accounts in Zernio profile settings |
| Posts not scheduling | Verify platform supports scheduling (not Reddit, Bluesky, Threads) |
| Rate limit errors | Reduce batch size, implement backoff retry |
| Auth failures | Regenerate API key (old key invalid) |

---

## Revenue Impact

- **Unified API**: Single integration vs multiple platform SDKs
- **Broader Coverage**: 14 platforms vs GHL Social Planner's limited set
- **Cost Savings**: Zernio pricing vs GHL add-on costs
- **Monetization**: Future customer add-on opportunity
- **Time Savings**: Agent content → direct publish (no manual workflow)

---

## Next Phases (Beyond v1)

- **White-Label Offering**: Resell Zernio scheduling to customers
- **Advanced Scheduling**: Leverage Zernio's queue/recurring features
- **Cross-Business Analytics**: Dashboard showing all Zernio posts/engagement
- **A/B Testing**: Schedule variants across platforms, track performance
- **AI-Powered Scheduling**: Use Zernio's queue feature + agent recommendations

---

**Questions?** See `.agents/tools/integrations/zernio.md` or ask the agent pool.
