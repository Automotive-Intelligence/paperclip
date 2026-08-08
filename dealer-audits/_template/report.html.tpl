<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<meta name="description" content="{{META_DESCRIPTION}}">
{{ROBOTS_META}}
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='14' fill='%23111'/><text x='50' y='68' font-size='52' text-anchor='middle' fill='white' font-family='Arial Black,sans-serif' font-weight='900'>AI</text></svg>">
<style>
  @font-face{font-family:'Archivo';src:url('/assets/Archivo.woff2') format('woff2');
    font-weight:100 900;font-stretch:62% 125%;font-display:swap}
  @font-face{font-family:'Inter Tight';src:url('/assets/InterTight.woff2') format('woff2');
    font-weight:100 900;font-display:swap}
  :root{
    --ink:#111111; --paper:#FFFFFF; --ground:#F1F1EE; --surface-2:#F7F7F4; --line:#E0E0DB;
    --muted:#5C5C57; --faint:#8B8B85;
    --crit:#C7362A; --crit-tint:#F9E6E3;
    --warn:#A97B08; --warn-tint:#F7EFD8;
    --good:#1E8F4E; --good-tint:#E4F2E9;
    --neutral:#6E6E68; --neutral-tint:#ECECE7;
    --hero:#111111; --hero-line:#2E2E2A;
    --shadow:0 1px 2px rgba(17,17,17,.05),0 10px 26px -14px rgba(17,17,17,.22);
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
  @media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{animation:none!important;transition:none!important}}
  body{margin:0;background:var(--ground);color:var(--ink);
    font:400 17px/1.65 'Inter Tight',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
  .display{font-family:'Archivo','Arial Black',Arial,sans-serif;font-stretch:114%;
    letter-spacing:-.015em;line-height:1;font-weight:900}
  .mono{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
  h1,h2,h3{text-wrap:balance}
  a{color:inherit}
  a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid currentColor;outline-offset:3px}
  .wrap{max-width:980px;margin:0 auto;padding:0 24px}

  /* site header */
  .site{background:var(--ink);color:#fff}
  .site .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 24px}
  .site img{height:40px;width:auto;display:block}
  .site .cta{background:#fff;color:var(--ink);text-decoration:none;font-weight:700;font-size:13.5px;
    padding:9px 15px;border-radius:8px;white-space:nowrap}
  .site .cta:hover{background:#e9e9e4}

  /* hero */
  .hero{background:var(--hero);color:#EDEDE8;border-top:1px solid var(--hero-line);position:relative;overflow:hidden}
  .hero::before{content:"";position:absolute;inset:0;pointer-events:none;
    background:linear-gradient(transparent 0,transparent 31px,rgba(255,255,255,.035) 32px);background-size:100% 32px}
  .hero .wrap{position:relative;padding:48px 24px 44px}
  .eyebrow{font:700 12px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.22em;text-transform:uppercase;
    color:#9a9a94;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .eyebrow .dot{width:8px;height:8px;border-radius:50%;background:var(--crit);flex:0 0 auto;
    animation:pulse 2.4s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(199,54,42,.55)}70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
  .hero h1{font-size:clamp(34px,6.4vw,62px);margin:18px 0 0;color:#fff;text-transform:uppercase}
  .hero .sub{color:#b3b3ad;font-size:16.5px;margin-top:14px;max-width:60ch}
  .metarow{margin-top:24px;display:flex;flex-wrap:wrap;gap:8px 26px;
    font:500 13px/1.6 ui-monospace,Menlo,monospace;color:#8f8f89}
  .metarow b{color:#fff;font-weight:600}
  .verdict{margin-top:28px;display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center;
    background:rgba(255,255,255,.045);border:1px solid var(--hero-line);
    border-left:4px solid var(--crit);border-radius:14px;padding:20px 22px}
  .gauge{width:96px;height:96px;border-radius:50%;display:grid;place-items:center;
    background:conic-gradient(var(--crit) 0 {{GAUGE_PCT}}%, rgba(255,255,255,.12) {{GAUGE_PCT}}% 100%);position:relative}
  .gauge::after{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--hero)}
  .gauge span{position:relative;z-index:1;text-align:center;line-height:1.05}
  .gauge .g{display:block;font:900 30px/1 'Archivo',Arial,sans-serif;color:#fff}
  .gauge .l{display:block;margin-top:3px;font:600 9.5px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.14em;color:#9a9a94;text-transform:uppercase}
  .verdict h2{font-size:19px;margin:0 0 6px;color:#fff}
  .verdict p{margin:0;color:#b3b3ad;font-size:14.5px;line-height:1.55}
  @media (max-width:620px){.verdict{grid-template-columns:1fr}}

  /* sections */
  section{padding:46px 0}
  section + section{border-top:1px solid var(--line)}
  .kicker{font:700 12px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.2em;text-transform:uppercase;
    color:var(--muted);margin:0 0 10px}
  h2.sec{font-size:clamp(23px,3.6vw,31px);margin:0 0 8px;text-transform:uppercase}
  .lead{color:var(--muted);font-size:17px;max-width:66ch;margin:0 0 4px}

  .thesis{background:var(--paper);border:2px solid var(--ink);border-radius:16px;
    padding:24px 26px;box-shadow:6px 6px 0 var(--ink);margin-top:12px}
  .thesis .tl{font:700 11.5px/1.6 ui-monospace,Menlo,monospace;letter-spacing:.18em;text-transform:uppercase;color:var(--crit);margin:0 0 8px}
  .thesis p{margin:0;font-size:18.5px;line-height:1.52;font-weight:500}
  .note{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:16px 18px;
    font-size:14px;color:var(--muted);line-height:1.6;margin-top:22px}
  .note b{color:var(--ink)}

  /* scorecard */
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;margin-top:22px}
  .card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow)}
  .card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
  .card h3{font-size:15.5px;margin:2px 0 0;font-weight:700;max-width:15ch}
  .card p{margin:12px 0 0;font-size:13.5px;color:var(--muted);line-height:1.5}
  .grade{font:900 19px/1 'Archivo',Arial,sans-serif;padding:8px 11px;border-radius:9px;min-width:44px;text-align:center}
  .g-crit{background:var(--crit-tint);color:var(--crit)}
  .g-warn{background:var(--warn-tint);color:var(--warn)}
  .g-good{background:var(--good-tint);color:var(--good)}
  .g-neutral{background:var(--neutral-tint);color:var(--neutral)}

  /* chips */
  .chip{display:inline-flex;align-items:center;font:700 11px/1.6 ui-monospace,Menlo,monospace;
    letter-spacing:.06em;padding:5px 10px;border-radius:999px;text-transform:uppercase;white-space:nowrap}
  .p0{background:var(--crit);color:#fff}
  .p1{background:var(--warn-tint);color:var(--warn)}
  .p2{background:var(--neutral-tint);color:var(--neutral)}

  /* findings */
  .find{background:var(--paper);border:1px solid var(--line);border-radius:14px;
    box-shadow:var(--shadow);margin-top:16px;overflow:hidden;border-left:4px solid var(--line)}
  .find.sev-crit{border-left-color:var(--crit)}
  .find.sev-warn{border-left-color:var(--warn)}
  .find.sev-low{border-left-color:var(--neutral)}
  .find .fh{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;
    padding:18px 22px 4px;flex-wrap:wrap}
  .find h3{font-size:19px;margin:0;max-width:44ch}
  .find .body{padding:6px 22px 20px}
  .row{display:grid;grid-template-columns:104px 1fr;gap:6px 16px;padding:9px 0;border-top:1px solid var(--line)}
  .row:first-child{border-top:0}
  .row .k{font:600 11.5px/1.7 ui-monospace,Menlo,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);padding-top:2px}
  .row .v{font-size:15px;line-height:1.55}
  .impact-hi{color:var(--crit);font-weight:700}
  .impact-med{color:var(--warn);font-weight:700}
  @media (max-width:620px){.row{grid-template-columns:1fr;gap:2px}.row .k{padding-top:6px}}

  /* table */
  .tablewrap{overflow-x:auto;margin-top:18px;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
  table{border-collapse:collapse;width:100%;min-width:520px;background:var(--paper);font-size:14.5px}
  th,td{text-align:left;padding:12px 16px;border-top:1px solid var(--line);vertical-align:top}
  thead th{border-top:0;background:var(--surface-2);font:600 12px/1.4 ui-monospace,Menlo,monospace;
    letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  tbody td:first-child{font-weight:700}
  .you td{background:var(--neutral-tint)}
  .num{font-variant-numeric:tabular-nums}

  /* plan */
  .phase{display:grid;grid-template-columns:150px 1fr;gap:20px;padding:22px 0;border-top:1px solid var(--line)}
  .phase:first-of-type{border-top:0}
  .phase .num{font:900 36px/1 'Archivo',Arial,sans-serif;color:var(--ink)}
  .phase .lbl{font-size:13.5px;color:var(--muted);margin-top:2px;font-weight:600}
  .phase ul{margin:0;padding:0;list-style:none;display:grid;gap:9px}
  .phase li{position:relative;padding-left:26px;font-size:15.5px;line-height:1.5}
  .phase li::before{content:"";position:absolute;left:0;top:8px;width:8px;height:8px;background:var(--ink);transform:rotate(45deg)}
  @media (max-width:620px){.phase{grid-template-columns:1fr;gap:8px}}

  /* CTA + lead form */
  .cta-block{background:var(--ink);color:#EDEDE8;border-radius:18px;padding:36px;margin-top:8px;position:relative;overflow:hidden}
  .cta-block h2{color:#fff;font-size:clamp(24px,4vw,32px);margin:0 0 12px;text-transform:uppercase}
  .cta-block p{color:#b3b3ad;margin:0 0 8px;max-width:64ch}
  .steps{display:flex;flex-wrap:wrap;gap:12px;margin-top:22px}
  .step{background:rgba(255,255,255,.055);border:1px solid var(--hero-line);border-radius:12px;padding:14px 16px;flex:1 1 200px}
  .step .p{font:700 12px/1.6 ui-monospace,Menlo,monospace;color:#9a9a94;letter-spacing:.08em}
  .step .t{color:#fff;font-weight:700;margin-top:6px;font-size:15px}
  .step .d{color:#a5a5a0;font-size:13px;margin-top:4px}
  .leadform{margin-top:26px;border-top:1px solid var(--hero-line);padding-top:24px}
  .leadform .fgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media (max-width:620px){.leadform .fgrid{grid-template-columns:1fr}}
  .leadform label{display:block;font:700 11px/1.6 ui-monospace,Menlo,monospace;
    letter-spacing:.1em;text-transform:uppercase;color:#9a9a94}
  .leadform label.full{margin-top:12px}
  .leadform input,.leadform textarea{display:block;width:100%;margin-top:6px;
    background:rgba(255,255,255,.07);border:1px solid var(--hero-line);border-radius:9px;
    color:#fff;font:400 15.5px/1.4 'Inter Tight',sans-serif;padding:11px 13px}
  .leadform input::placeholder,.leadform textarea::placeholder{color:#6e6e68}
  .leadform textarea{min-height:84px;resize:vertical}
  .leadform .hp{position:absolute;left:-9999px;top:-9999px;height:1px;width:1px;opacity:0}
  .bookbtn{display:inline-block;background:#fff;color:var(--ink);text-decoration:none;font-weight:800;
    border:0;cursor:pointer;padding:14px 22px;border-radius:10px;margin-top:18px;font-size:15.5px;
    font-family:'Inter Tight',sans-serif}
  .bookbtn:hover{background:#e9e9e4}
  .bookbtn[disabled]{opacity:.6;cursor:wait}
  .formnote{font-size:13px;color:#8f8f89;margin-top:12px}
  .formnote a{color:#fff}
  .formstate{font-size:14.5px;font-weight:600;margin-top:12px;display:none}
  .formstate.err{display:block;color:#f2998c}
  .formstate.ok{display:block;color:#7ed09e}

  .sources{font-size:13.5px;color:var(--muted)}
  .sources ul{margin:12px 0 0;padding-left:18px;display:grid;gap:6px}
  .sources a{word-break:break-word}
  footer{background:var(--ink);color:#9a9a94;font-size:14px;margin-top:24px}
  footer .wrap{padding:32px 24px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}
  footer a{color:#fff;text-decoration:none;font-weight:600}
</style>
</head>
<body>

<header class="site">
  <div class="wrap">
    <a href="/" aria-label="Automotive Intelligence home"><img src="/assets/avi-logo.png" alt="Automotive Intelligence"></a>
    <a class="cta" href="#book">Book a free assessment</a>
  </div>
</header>

<div class="hero">
  <div class="wrap">
    <p class="eyebrow"><span class="dot" aria-hidden="true"></span> Digital Diagnostic Report · Automotive Intelligence</p>
    <h1 class="display">{{HERO_TITLE_HTML}}</h1>
    <p class="sub">{{HERO_SUB}}</p>
    <div class="metarow">{{META_ROWS}}</div>
    <div class="verdict">
      <div class="gauge" aria-hidden="true"><span><span class="g">{{GRADE}}</span><span class="l">Overall</span></span></div>
      <div>
        <h2>{{VERDICT_TITLE}}</h2>
        <p>{{VERDICT_TEXT}}</p>
      </div>
    </div>
  </div>
</div>

<main>
  <!-- THESIS -->
  <section><div class="wrap">
    <p class="kicker">{{THESIS_KICKER}}</p>
    <h2 class="sec display">{{THESIS_HEADLINE}}</h2>
    <div class="thesis">
      <p class="tl">{{THESIS_LABEL}}</p>
      <p>{{THESIS_HTML}}</p>
    </div>
    <div class="note">{{NOTE_HTML}}</div>
  </div></section>

  <!-- SCORECARD -->
  <section><div class="wrap">
    <p class="kicker">Diagnostic scorecard</p>
    <h2 class="sec display">{{SCORECARD_HEADLINE}}</h2>
    <p class="lead">Grades read like dashboard warning lights: red is failing now, amber needs attention, grey is inconclusive without on-site access.</p>
    <div class="grid">
{{SCORECARD_CARDS}}
    </div>
  </div></section>

  <!-- CONTEXT -->
  <section><div class="wrap">
    <p class="kicker">Context</p>
    <h2 class="sec display">{{CONTEXT_HEADLINE}}</h2>
    <p class="lead">{{CONTEXT_LEAD_HTML}}</p>
    <div class="tablewrap">
      <table>
        <thead><tr>{{CONTEXT_TABLE_HEAD}}</tr></thead>
        <tbody>
{{CONTEXT_TABLE_ROWS}}
        </tbody>
      </table>
    </div>
    <p class="lead" style="margin-top:18px">{{CONTEXT_AFTER_HTML}}</p>
  </div></section>

  <!-- FINDINGS -->
  <section><div class="wrap">
    <p class="kicker">Findings</p>
    <h2 class="sec display">Ranked by what it costs you</h2>
{{FINDINGS_HTML}}
  </div></section>

  <!-- PLAN -->
  <section><div class="wrap">
    <p class="kicker">Roadmap</p>
    <h2 class="sec display">30 / 60 / 90-day action plan</h2>
{{PHASES_HTML}}
  </div></section>

  <!-- CTA -->
  <section id="book"><div class="wrap">
    <div class="cta-block">
      <h2 class="display">{{CTA_HEADLINE_HTML}}</h2>
      <p>{{CTA_TEXT_HTML}}</p>
      <div class="steps">
{{CTA_STEPS}}
      </div>
      <form id="lead-form" class="leadform" novalidate>
        <div class="fgrid">
          <label>Name*<input name="name" autocomplete="name" required placeholder="First Last"></label>
          <label>Work email<input name="email" type="email" autocomplete="email" placeholder="you@dealership.com"></label>
          <label>Phone<input name="phone" type="tel" autocomplete="tel" placeholder="(555) 555-5555"></label>
          <label>Dealership<input name="dealership" autocomplete="organization" placeholder="Store name"></label>
        </div>
        <label class="full">Anything specific?<textarea name="message" placeholder="What's going on with your digital presence?"></textarea></label>
        <input class="hp" name="company_website" tabindex="-1" autocomplete="off" aria-hidden="true">
        <button class="bookbtn" type="submit">Book the free assessment →</button>
        <p class="formnote">We reply within one business day. Name plus a phone or email is all we need. Prefer email? <a href="mailto:michael@automotiveintelligence.io?subject={{MAILTO_SUBJECT}}">michael@automotiveintelligence.io</a></p>
        <p class="formstate" role="status" aria-live="polite"></p>
      </form>
    </div>
  </div></section>

  <!-- SOURCES -->
  <section><div class="wrap sources">
    <p class="kicker">Sources</p>
    <ul>
{{SOURCES_HTML}}
    </ul>
  </div></section>
</main>

<footer>
  <div class="wrap">
    <span>© 2026 Automotive Intelligence · Digital Diagnostic Report</span>
    <a href="mailto:michael@automotiveintelligence.io">michael@automotiveintelligence.io</a>
  </div>
</footer>

<script>
(function () {
  var form = document.getElementById('lead-form');
  if (!form) return;
  var state = form.querySelector('.formstate');
  var btn = form.querySelector('button[type=submit]');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var d = new FormData(form);
    var body = {
      name: d.get('name') || '', email: d.get('email') || '', phone: d.get('phone') || '',
      dealership: d.get('dealership') || '', message: d.get('message') || '',
      company_website: d.get('company_website') || '',
      source: '{{FORM_SOURCE}}'
    };
    state.className = 'formstate';
    if (!body.name.trim() || (!body.email.trim() && !body.phone.trim())) {
      state.textContent = 'Please add your name and a phone or email so we can reach you.';
      state.className = 'formstate err';
      return;
    }
    btn.disabled = true; btn.textContent = 'Sending…';
    fetch('/api/lead', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok && j.ok }; }); })
      .then(function (res) {
        if (res.ok) {
          form.innerHTML = '<p class="formstate ok" style="display:block;font-size:17px">Got it — we\'ll be in touch within one business day to schedule your assessment.</p>';
        } else { throw new Error('ingest'); }
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = 'Book the free assessment →';
        state.innerHTML = 'That didn\'t go through. Email us directly at <a href="mailto:michael@automotiveintelligence.io" style="color:#fff">michael@automotiveintelligence.io</a> and we\'ll take it from there.';
        state.className = 'formstate err';
      });
  });
})();
</script>
</body>
</html>
