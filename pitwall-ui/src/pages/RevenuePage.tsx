import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, RevenueBoard } from '../lib/api';

// Michael's brief: "I don't want to see what is and what went silent, I just want to
// know what needs our hands to get us revenue." So: no status, no charts, no counts of
// things that are merely true. Every card is an action with money attached, and a card
// only exists while it is actionable. An empty board is a real answer, not a gap.

const BUCKET: Record<number, { label: string; tone: string }> = {
  0: { label: 'Money waiting', tone: 'border-pitred/40 bg-pitred/10' },
  1: { label: 'Opportunity open', tone: 'border-pitgreen/40 bg-pitgreen/10' },
  2: { label: 'Needs your call', tone: 'border-pitamber/40 bg-pitamber/10' },
  3: { label: 'Rail down', tone: 'border-pitborder bg-pitsunk/60' },
};

const OWNER_TONE = (o: string) =>
  o.toLowerCase().includes('michael') ? 'text-pitamber border-pitamber/40' : 'text-pitmuted border-pitborder';

export default function RevenuePage() {
  const [data, setData] = useState<RevenueBoard | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .revenueBoard()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load the board: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Reading live state…</div>;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">What needs our hands</h1>
        <p className="text-sm text-pitfaint">
          Actions with money attached. Nothing here is status; if it is on this board,
          doing it moves revenue.
        </p>
      </header>

      {data.items.length === 0 && (
        <div className="rounded-lg border border-pitgreen/40 bg-pitgreen/10 p-6 text-center">
          <p className="text-sm text-pitgreen">{data.empty_message}</p>
        </div>
      )}

      <div className="space-y-3">
        {data.items.map((it, i) => {
          const b = BUCKET[it.priority] ?? BUCKET[3];
          return (
            <div key={i} className={`rounded-lg border p-4 ${b.tone}`}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-pitmuted">
                  {b.label}
                </span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${OWNER_TONE(it.owner)}`}>
                  {it.owner}
                </span>
              </div>

              <h2 className="text-[15px] font-semibold leading-snug text-pittext">{it.title}</h2>
              <p className="mt-1 text-xs leading-relaxed text-pitmuted">{it.why_money}</p>

              <div className="mt-3 rounded-md border border-pitborder bg-pitsunk/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-pitfaint">Do this</div>
                <div className="text-sm text-pittext">{it.next_action}</div>
              </div>

              {(it.detail || it.where) && (
                <p className="mt-2 text-[11px] text-pitfaint">
                  {it.detail}
                  {it.detail && it.where ? ' · ' : ''}
                  {it.where && <span className="font-mono">{it.where}</span>}
                </p>
              )}
            </div>
          );
        })}
      </div>

      {data.sources_failed.length > 0 && (
        <p className="text-[11px] text-pitamber">
          Could not read: {data.sources_failed.join(', ')}. This board may be missing items,
          so treat it as incomplete rather than clear.
        </p>
      )}
    </motion.div>
  );
}
