# Thank You Pages — Handoff Checklist

**Status as of 2026-04-18 7:20 PM:**
- App live on Railway: https://thank-you-pages-production.up.railway.app
- Preview each business: add `?b=ai`, `?b=cd`, or `?b=apg` to the URL above
- 3 custom subdomains created in Railway, **waiting on DNS records**

## What you (Michael) need to do

### 1. Add DNS records (3 registrars, ~5 minutes total)

For each domain, add **both** a CNAME and a TXT record exactly as listed.
TTL: leave at default (or set to 300 for faster propagation).

#### automotiveintelligence.io
| Type | Name | Value |
|---|---|---|
| CNAME | `book` | `iyz896tm.up.railway.app` |
| TXT | `_railway-verify.book` | `railway-verify=railway-verify=9b144c263267c5d3cac99530b5aee9328c5add747fe45b7ff0056a8456710012` |

#### calling.digital (Squarespace)
| Type | Name | Value |
|---|---|---|
| CNAME | `book` | `75v43lp9.up.railway.app` |
| TXT | `_railway-verify.book` | `railway-verify=railway-verify=a5e588e535899ed63178107904d05be01cdf4b221afd4871af55b0975d390a7b` |

#### aiphoneguy.ai
| Type | Name | Value |
|---|---|---|
| CNAME | `book` | `v1bmva4x.up.railway.app` |
| TXT | `_railway-verify.book` | `railway-verify=railway-verify=0ca3a3b90c94c2295c6c34570eb7233c6a6fcc9b25003c9cbf92a9038c8efbb7` |

DNS typically propagates in 5–60 minutes. Railway auto-provisions SSL once the records verify.

### 2. Wire the thank you pages into your calendars

#### Calendly (Automotive Intelligence + Calling Digital)
Free plan doesn't support auto-redirect after booking. Two options:

**Option A (free):** Add a link to the confirmation email template.
- Calendly → Event type → Workflows → "Send email when event is scheduled"
- Add a line: `Here's what happens next: https://book.automotiveintelligence.io` (or `book.calling.digital` for the other event)

**Option B ($12/mo):** Upgrade Calendly to Standard and set the redirect URL.
- Event type → Booking Page → "Confirmation" → "Redirect to an external site"
- URL: your `book.<domain>` link

#### GoHighLevel (AI Phone Guy) — free redirect
- Calendar settings → Confirmation → "Redirect to external URL"
- URL: `https://book.aiphoneguy.ai`

### 3. Verify each page is live (after DNS propagates)

Open each URL in an incognito window:
- https://book.automotiveintelligence.io
- https://book.calling.digital
- https://book.aiphoneguy.ai

Each should load the correct branded "You're booked" page.

## Troubleshooting

- **Page not loading after 2 hours:** check DNS with `dig book.<domain>` and confirm the CNAME resolves to the `*.up.railway.app` target.
- **SSL certificate error:** Railway auto-provisions Let's Encrypt after DNS verifies; usually 5-10 minutes after the records land. If it's been over an hour, check the domain in the Railway dashboard.
- **Wrong business shows on a URL:** hostname mismatch in `thankyou/src/businesses.ts`. Open an issue or ping.

## What's deferred (not needed tonight)

- Analytics (GA4 or Plausible per subdomain)
- Stats bar, testimonials, video content
- Per-prospect UTM tracking

## How to edit content later

All copy lives in one file: `thankyou/src/businesses.ts`
Edit → `npm run build` in `thankyou/` → `railway up --detach` → live in ~1 minute.
