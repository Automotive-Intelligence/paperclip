"""CMG audit: pull Klaviyo welcome templates + forms + lists + flows + segments.

Read-only. Run from ~/paperclip so tools.klaviyo can find .env.

Usage: python3 scripts/pp_cmg_audit.py
"""
import sys, os, re
from pathlib import Path
sys.path.insert(0, os.path.expanduser('~/paperclip'))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(os.path.expanduser('~/paperclip')) / '.env', override=True)
except ImportError:
    pass

from tools.klaviyo import _klaviyo_request

IMG_RE = re.compile(r'<img[^>]*src=["\']([^"\']+)', re.IGNORECASE)
BG_RE  = re.compile(r"url\(['\"]?([^)'\"]+)")

TEMPLATES = {'W1':'VX9hrw','W2':'YhWXde','W3':'XEVUAB','W4':'Tt56X2','W5':'UCtVQS'}

print('=== Welcome template image refs ===')
for label, tid in TEMPLATES.items():
    r = _klaviyo_request('GET', 'paperandpurpose', f'/templates/{tid}/')
    if isinstance(r, dict):
        html = r.get('data', {}).get('attributes', {}).get('html', '') or ''
        imgs = IMG_RE.findall(html)
        bgs  = BG_RE.findall(html)
        all_urls = imgs + bgs
        print(f'{label} ({tid}): {len(all_urls)} image refs, html_len={len(html)}')
        for u in all_urls[:8]:
            print(f'   {u[:140]}')
    else:
        print(f'{label}: ERR {str(r)[:160]}')

print()
print('=== Forms ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/forms/')
if isinstance(r, dict):
    for f in r.get('data', []):
        a = f.get('attributes', {})
        print(f"  id={f.get('id')}  status={a.get('status')}  name={a.get('name')}  updated={a.get('updated_at','')[:10]}")

print()
print('=== Lists ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/lists/')
if isinstance(r, dict):
    for l in r.get('data', []):
        a = l.get('attributes', {})
        print(f"  id={l.get('id')}  name={a.get('name')}  created={a.get('created','')[:10]}")

print()
print('=== Flows ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/flows/')
if isinstance(r, dict):
    for fl in r.get('data', []):
        a = fl.get('attributes', {})
        print(f"  id={fl.get('id')}  status={a.get('status')}  trigger={a.get('trigger_type')}  name={a.get('name')}")

print()
print('=== DataMoon ICP list sizes ===')
for lid, label in [('TMSfVN','PP ICP-A Returning Woman'), ('VAi9sh','PP ICP-B Christian Girly'), ('WaPbWx','PP Site Visitor Retargeting'), ('USLsqg','Be Transformed - Pre-launch'), ('SsMyVv','Email List')]:
    r = _klaviyo_request('GET', 'paperandpurpose', f'/lists/{lid}/?additional-fields[list]=profile_count')
    if isinstance(r, dict):
        attrs = r.get('data', {}).get('attributes', {})
        cnt = attrs.get('profile_count', '?')
        print(f"  {label} ({lid}): profile_count={cnt}")
    else:
        print(f"  {label}: ERR {str(r)[:120]}")

print()
print('=== Campaigns (recent) ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/campaigns/?filter=equals(messages.channel,\'email\')&sort=-created_at&page[size]=20')
if isinstance(r, dict):
    for c in r.get('data', [])[:20]:
        a = c.get('attributes', {})
        print(f"  [{a.get('status','?')}]  created={a.get('created_at','')[:10]}  send_time={(a.get('send_time') or '')[:10]}  name={a.get('name','?')}")
else:
    print('ERR:', str(r)[:300])

print()
print('=== Form details (Email Embed) ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/forms/TsgPh7/')
import json
print(json.dumps(r, indent=2)[:2000] if isinstance(r, dict) else str(r)[:300])

print()
print('=== Form versions (capture fields, list, success behavior) ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/forms/TsgPh7/form-versions/')
if isinstance(r, dict):
    for v in r.get('data', []):
        a = v.get('attributes', {})
        print(f"  id={v.get('id')}  status={a.get('status')}  updated={a.get('updated_at','')[:10]}")
        cfg = a.get('form_components') or a.get('components') or {}
        print('  KEYS:', list(a.keys()))

print()
print('=== Welcome series flow detail (Be Transformed - Welcome Series) ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/flows/VSHLbe/?include=flow-actions')
if isinstance(r, dict):
    data = r.get('data', {})
    attrs = data.get('attributes', {})
    print(f"  name={attrs.get('name')}")
    print(f"  trigger={attrs.get('trigger_type')}")
    print(f"  status={attrs.get('status')}")
    print(f"  created={attrs.get('created','')[:10]}  updated={attrs.get('updated','')[:10]}")
    inc = r.get('included', [])
    print(f"  flow actions in 'included': {len(inc)}")
    for a in inc[:8]:
        aa = a.get('attributes', {})
        print(f"    action_type={aa.get('action_type')}  status={aa.get('status')}  name={aa.get('name')}")

print()
print('=== Segments ===')
r = _klaviyo_request('GET', 'paperandpurpose', '/segments/')
if isinstance(r, dict):
    for s in r.get('data', []):
        a = s.get('attributes', {})
        print(f"  id={s.get('id')}  name={a.get('name')}")
