#!/usr/bin/env python3
"""Create + publish the gated free-prompts lead-magnet page on the P&P Shopify store.
Usage: python3 scripts/pp_create_freebie_page.py <SHOPIFY_TOKEN>
Gated: email capture -> Klaviyo list TKvgFD -> reveals PDF download.
"""
import sys, requests

TOKEN = sys.argv[1]
SHOP = "nsapaq-qu.myshopify.com"
PDF = "https://cdn.shopify.com/s/files/1/0712/6168/3800/files/pp_5_prompts_to_renew_your_mind.pdf?v=1780974368"
KLAVIYO_PUBLIC = "S343ab"
LIST_ID = "TKvgFD"

BODY = """
<div style="max-width:620px;margin:0 auto;padding:40px 20px;font-family:Georgia,'Times New Roman',serif;color:#4A5340;text-align:center;">
  <p style="letter-spacing:3px;text-transform:uppercase;font-size:12px;color:#B89968;margin-bottom:8px;">Paper &amp; Purpose</p>
  <h1 style="font-size:34px;font-weight:normal;line-height:1.2;margin:0 0 16px;color:#4A5340;">5 Prompts to Renew Your Mind</h1>
  <p style="font-size:17px;line-height:1.6;color:#4A5340;max-width:480px;margin:0 auto 28px;">
    A free set of guided prompts to help you notice the thoughts that do not match what God says, and gently replace them with scripture. Rooted in Romans 12:2. Enter your name and email and it is yours.
  </p>
  <div id="pp-gate" style="background:#F2EDE4;border:1px solid #D4B5A8;border-radius:12px;padding:28px 24px;max-width:420px;margin:0 auto;">
    <form id="pp-form">
      <input name="name" placeholder="First name" required style="width:100%;box-sizing:border-box;padding:13px 14px;margin-bottom:12px;border:1px solid #9CA88E;border-radius:8px;font-size:16px;font-family:inherit;color:#4A5340;background:#fff;">
      <input name="email" type="email" placeholder="Email address" required style="width:100%;box-sizing:border-box;padding:13px 14px;margin-bottom:16px;border:1px solid #9CA88E;border-radius:8px;font-size:16px;font-family:inherit;color:#4A5340;background:#fff;">
      <button type="submit" style="width:100%;padding:14px;background:#B89968;color:#fff;border:none;border-radius:8px;font-size:16px;letter-spacing:.5px;font-family:inherit;cursor:pointer;">Send me the free prompts</button>
    </form>
    <p style="font-size:12px;color:#9CA88E;margin:14px 0 0;">No spam, just a gentle note now and then. Unsubscribe anytime.</p>
  </div>
  <div id="pp-download" style="display:none;background:#F2EDE4;border:1px solid #D4B5A8;border-radius:12px;padding:32px 24px;max-width:420px;margin:0 auto;">
    <h2 style="font-size:24px;font-weight:normal;color:#4A5340;margin:0 0 12px;">Your prompts are ready</h2>
    <p style="font-size:16px;color:#4A5340;margin:0 0 20px;">Thank you. Tap below to open your free 5 prompts.</p>
    <a href="__PDF__" target="_blank" rel="noopener" style="display:inline-block;padding:14px 28px;background:#4A5340;color:#fff;text-decoration:none;border-radius:8px;font-size:16px;">Download the PDF</a>
  </div>
</div>
<script>
(function(){
  var f=document.getElementById('pp-form');
  if(!f){return;}
  f.addEventListener('submit',function(e){
    e.preventDefault();
    var email=f.querySelector('input[name=\\"email\\"]').value;
    var name=f.querySelector('input[name=\\"name\\"]').value;
    var btn=f.querySelector('button'); btn.disabled=true; btn.textContent='Sending...';
    fetch('https://a.klaviyo.com/client/subscriptions/?company_id=__PUB__',{
      method:'POST',
      headers:{'revision':'2024-10-15','content-type':'application/json'},
      body:JSON.stringify({data:{type:'subscription',attributes:{profile:{data:{type:'profile',attributes:{email:email,first_name:name}}}},relationships:{list:{data:{type:'list',id:'__LIST__'}}}}})
    }).catch(function(){}).then(function(){
      document.getElementById('pp-gate').style.display='none';
      document.getElementById('pp-download').style.display='block';
    });
  });
})();
</script>
""".replace("__PDF__", PDF).replace("__PUB__", KLAVIYO_PUBLIC).replace("__LIST__", LIST_ID)

payload = {"page": {"title": "Free: 5 Prompts to Renew Your Mind",
                    "handle": "free-prompts",
                    "body_html": BODY,
                    "published": True}}
r = requests.post(f"https://{SHOP}/admin/api/2024-10/pages.json",
                  headers={"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"},
                  json=payload, timeout=30)
if r.status_code in (200, 201):
    p = r.json()["page"]
    print("CREATED + PUBLISHED:", f"https://paperandpurpose.co/pages/{p['handle']}")
    print("published_at:", p.get("published_at"))
else:
    print("ERROR", r.status_code, r.text[:400])
