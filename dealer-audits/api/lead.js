// Vercel serverless function: dealer-audits lead capture → paperclip /lead/ingest
// (the funnel system of record: durable store first, then CRM push, human always
// alerted, fail-closed). We mirror its contract: on ok=false we tell the visitor
// to email us directly instead of pretending success.

const INGEST_URL =
  process.env.LEAD_INGEST_URL ||
  'https://paperclip-production-ba14.up.railway.app/lead/ingest';

const MAX = { name: 200, phone: 50, email: 200, dealership: 200, message: 2000, source: 100 };

function clean(value, cap) {
  return String(value ?? '').trim().slice(0, cap);
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ ok: false, error: 'POST only' });
  }

  const b = req.body || {};

  // Honeypot: real visitors never fill this hidden field. Pretend success so
  // bots don't learn they were caught.
  if (b.company_website) return res.status(200).json({ ok: true });

  const name = clean(b.name, MAX.name);
  const phone = clean(b.phone, MAX.phone);
  const email = clean(b.email, MAX.email);
  const dealership = clean(b.dealership, MAX.dealership);
  const message = clean(b.message, MAX.message);
  const source = clean(b.source, MAX.source) || 'dealer-audits-site';

  if (!name || (!phone && !email)) {
    return res.status(400).json({ ok: false, error: 'Name and a phone or email are required.' });
  }

  const payload = {
    brand: 'avi',
    name,
    phone,
    email,
    trade: '',
    message: [dealership && `Dealership: ${dealership}`, message].filter(Boolean).join('\n'),
    source,
  };

  try {
    const upstream = await fetch(INGEST_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000),
    });
    const data = await upstream.json().catch(() => ({}));
    if (upstream.ok && data.ok) return res.status(200).json({ ok: true });
    // Fail closed, like the system of record: stored-but-unalerted (or worse)
    // is not success.
    return res.status(502).json({ ok: false, error: 'Lead intake is degraded.' });
  } catch (err) {
    return res.status(502).json({ ok: false, error: 'Lead intake is unreachable.' });
  }
}
