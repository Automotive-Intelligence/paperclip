import os, re, sys, time
sys.path.insert(0, os.path.expanduser("~/paperclip/tools"))
import ghl

IMG_BASE = "https://ai-phone-guy-site.vercel.app/blog-images/"
# post_id, slug, image_name
POSTS = [
    ("6a3c28149a4fee011b6d5efd", "what-a-missed-call-really-costs-a-home-services-business", "missed-call"),
    ("6a3c28159a4fee8f206d5f23", "answering-service-vs-ai-receptionist-which-is-right-for-a-service-business", "answering-vs-ai"),
    ("6a3c2815afa186110e3835ad", "how-to-never-miss-an-after-hours-service-call", "after-hours"),
    ("6a3c28160b59403682cb987f", "5-signs-your-business-is-leaking-jobs-to-voicemail", "voicemail-leak"),
]

try:
    import markdown as _md
    def to_html(b): return _md.markdown(b, extensions=["extra", "sane_lists"])
except Exception:
    def to_html(b):
        out = []
        for blk in re.split(r"\n\s*\n", b.strip()):
            s = blk.strip()
            if not s: continue
            if s.startswith("## "): out.append(f"<h2>{s[3:].strip()}</h2>")
            elif all(l.strip().startswith(("- ", "* ")) for l in s.splitlines() if l.strip()):
                out.append("<ul>" + "".join(f"<li>{l.strip()[2:].strip()}</li>" for l in s.splitlines() if l.strip()) + "</ul>")
            else: out.append("<p>" + s.replace("\n", " ") + "</p>")
        return "".join(out)

# parse blog batch into slug -> (title, html, desc)
raw = open(os.path.expanduser("~/avo-telemetry/marketing_deliverables/aipg_blog_batch_2026-06-24.md"), encoding="utf-8").read()
parts = [c for c in re.split(r"^---ARTICLE \d+---\s*$", raw, flags=re.M) if c.strip()]
by_slug = {}
for c in parts:
    seg = re.split(r"^---\s*$", c, maxsplit=2, flags=re.M)
    fm = seg[1] if len(seg) >= 3 else ""
    body = seg[2].strip() if len(seg) >= 3 else c.strip()
    mt = re.search(r'title:\s*"(.*?)"', fm); md = re.search(r'description:\s*"(.*?)"', fm)
    title = mt.group(1) if mt else "Untitled"
    by_slug[ghl._slugify(title)] = (title, to_html(body), (md.group(1) if md else body[:160]))

PUBLISH = os.getenv("PUBLISH") == "1"
loc = os.getenv("GHL_LOCATION_ID", "").strip()
blog = os.getenv("GHL_BLOG_ID", "").strip()
a = ghl._ghl_request("ga", "GET", f"/blogs/authors/?locationId={loc}&limit=5&offset=0", timeout=15)
author = (a.get("authors") or [{}])[0].get("_id")
print(f"loc={bool(loc)} blog={blog} author={bool(author)} mode={'PUBLISH' if PUBLISH else 'DRY'}")

for pid, slug, img in POSTS:
    rec = by_slug.get(slug)
    image_url = IMG_BASE + img + ".png"
    print(f"\n[{pid}] {slug[:40]} -> {img}.png  matched_content={bool(rec)}")
    if not rec:
        print("   SKIP: no content match"); continue
    title, html_body, desc = rec
    payload = {
        "locationId": loc, "blogId": blog, "title": title, "rawHTML": html_body,
        "urlSlug": slug, "description": desc[:160], "imageUrl": image_url,
        "imageAltText": title, "author": author,
        "publishedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "status": "PUBLISHED",
    }
    if PUBLISH:
        try:
            d = ghl._ghl_request("upd", "PUT", f"/blogs/posts/{pid}", json_body=payload, timeout=25)
            bp = d.get("blogPost", d) if isinstance(d, dict) else {}
            print("   -> OK img=", bool(bp.get("imageUrl")), "url=", (bp.get("imageUrl") or "")[:60])
        except Exception as e:
            print("   -> ERROR:", str(e)[:200])
