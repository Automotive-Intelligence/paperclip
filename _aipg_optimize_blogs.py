import os, re, sys, json
sys.path.insert(0, os.path.expanduser("~/paperclip/tools"))
import ghl

DISCOVERY = "https://theaiphoneguy.ai/discovery"
BLOG = "https://theaiphoneguy.ai/blog/"
IMG = "https://ai-phone-guy-site.vercel.app/blog-images/"

# id, slug, image, title(short SEO), early_cta_line, final_cta_md
POSTS = [
 ("6a3c28149a4fee011b6d5efd",
  "what-a-missed-call-really-costs-a-home-services-business", "missed-call.png",
  "What a Missed Call Really Costs a Home-Services Business",
  "Curious what that number looks like for your shop? [Book a quick discovery call](%s) and we will run it together." % DISCOVERY,
  "You already know the phone is costing you work. The next step is seeing exactly how much, and plugging the leak. [Book a discovery call](%s) and we will walk through your numbers together, no pressure." % DISCOVERY),
 ("6a3c28159a4fee8f206d5f23",
  "answering-service-vs-ai-receptionist-which-is-right-for-a-service-business", "answering-vs-ai.png",
  "Answering Service vs AI Receptionist for Trades",
  "Not sure which one fits your shop? [Book a discovery call](%s) and we will talk it through." % DISCOVERY,
  "The fastest way to know which path fits your shop is to talk it through with someone who has set both up. [Book a discovery call](%s) and we will help you choose." % DISCOVERY),
 ("6a3c2815afa186110e3835ad",
  "how-to-never-miss-an-after-hours-service-call", "after-hours.png",
  "How to Never Miss an After-Hours Service Call",
  "Want your nights and weekends actually covered? [Book a discovery call](%s) to see how it would work for your shop." % DISCOVERY,
  "Your next big job might come in at 9 p.m. tonight. Make sure someone answers. [Book a discovery call](%s) and we will set up your after-hours coverage." % DISCOVERY),
 ("6a3c28160b59403682cb987f",
  "5-signs-your-business-is-leaking-jobs-to-voicemail", "voicemail-leak.png",
  "5 Signs Your Business Is Leaking Jobs to Voicemail",
  "Recognize a few of these signs already? [Book a discovery call](%s) and we will find the leak together." % DISCOVERY,
  "If those signs hit close to home, the leak is happening right now. [Book a discovery call](%s) and we will help you seal it." % DISCOVERY),
]
SLUG_TITLE = {p[1]: p[3] for p in POSTS}

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
            elif s.startswith("### "): out.append(f"<h3>{s[4:].strip()}</h3>")
            else:
                # naive markdown link -> anchor
                s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
                out.append("<p>" + s.replace("\n", " ") + "</p>")
        return "".join(out)

raw = open(os.path.expanduser("~/avo-telemetry/marketing_deliverables/aipg_blog_batch_2026-06-24.md"), encoding="utf-8").read()
parts = [c for c in re.split(r"^---ARTICLE \d+---\s*$", raw, flags=re.M) if c.strip()]

def parse_article(c):
    seg = re.split(r"^---\s*$", c, maxsplit=2, flags=re.M)
    fm, body = (seg[1], seg[2].strip()) if len(seg) >= 3 else ("", c.strip())
    title = (re.search(r'title:\s*"(.*?)"', fm) or [None, ""])[1]
    desc = (re.search(r'description:\s*"(.*?)"', fm) or [None, ""])[1]
    faqs = re.findall(r'-\s*q:\s*"(.*?)"\s*\n\s*a:\s*"(.*?)"', fm, flags=re.S)
    return ghl._slugify(title), title, desc, body, faqs

by_slug = {}
for c in parts:
    s, t, d, b, f = parse_article(c)
    by_slug[s] = (t, d, b, [(q.strip(), a.strip()) for q, a in f])

PUBLISH = os.getenv("PUBLISH") == "1"
loc = os.getenv("GHL_LOCATION_ID", "").strip()
blog = os.getenv("GHL_BLOG_ID", "").strip()
a = ghl._ghl_request("ga", "GET", f"/blogs/authors/?locationId={loc}&limit=5&offset=0", timeout=15)
author = (a.get("authors") or [{}])[0].get("_id")
print(f"blog={blog} author={bool(author)} mode={'PUBLISH' if PUBLISH else 'DRY'}")

for pid, slug, img, seo_title, early_cta, final_cta in POSTS:
    rec = by_slug.get(slug)
    if not rec:
        print(f"[{slug[:30]}] NO MATCH"); continue
    _t, desc, body, faqs = rec

    # 1) insert early CTA after the 2nd paragraph
    paras = re.split(r"\n\s*\n", body.strip())
    paras.insert(min(2, len(paras)), f"\n> {early_cta}\n")  # blockquote-styled CTA
    body2 = "\n\n".join(paras)

    # 2) related reading (other 3 posts)
    related = [f"- [{SLUG_TITLE[s]}]({BLOG}{s})" for s in SLUG_TITLE if s != slug]
    related_md = "## Keep reading\n\n" + "\n".join(related)

    # 3) FAQ section (visible content)
    faq_md = "## Frequently asked questions\n\n" + "\n\n".join(
        f"### {q}\n\n{a}" for q, a in faqs)

    # 4) final CTA
    final_md = "## See it on your own calls\n\n" + final_cta

    full_md = "\n\n".join([body2, final_md, related_md, faq_md])
    body_html = to_html(full_md)

    # 5) FAQPage + Article JSON-LD (appended; GHL may strip <script> but harmless)
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": q,
                              "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs]}
    body_html += '<script type="application/ld+json">' + json.dumps(faq_ld) + "</script>"

    payload = {
        "locationId": loc, "blogId": blog, "title": seo_title, "rawHTML": body_html,
        "urlSlug": slug, "description": desc[:160], "imageUrl": IMG + img,
        "imageAltText": seo_title, "author": author,
        "publishedAt": "2026-06-24T12:00:00.000Z", "status": "PUBLISHED",
    }
    cta_count = body_html.count(DISCOVERY)
    print(f"\n[{slug[:34]}] ctas={cta_count} faqs={len(faqs)} related={len(related)} words~{len(full_md.split())}")
    if PUBLISH:
        try:
            d = ghl._ghl_request("opt", "PUT", f"/blogs/posts/{pid}", json_body=payload, timeout=30)
            bp = d.get("updatedBlogPost", d)
            print("   -> OK img=", bool(bp.get("imageUrl")), "title=", (bp.get("title") or "")[:40])
        except Exception as e:
            print("   -> ERROR:", str(e)[:200])
