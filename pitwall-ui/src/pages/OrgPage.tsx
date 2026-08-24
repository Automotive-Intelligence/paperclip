import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, SeatHealth } from '../lib/api';

// Seat coordination health. AVO's seats talk by writing "FLAG FOR: <seat>" into their
// own state file and pull-scanning each other's. The silent failure: a flag posted to a
// seat that has gone quiet is mail nobody opens. Nothing errors, so nobody finds out.
// This page exists to make that visible, and it leads with the unread count rather than
// with a tidy org chart, because the unread count is the part that costs work.

function ageLabel(d: number | null): string {
  if (d === null || d === undefined) return 'never';
  if (d < 1) return 'today';
  return `${Math.round(d)}d ago`;
}

export default function OrgPage() {
  const [data, setData] = useState<SeatHealth | null>(null);
  const [error, setError] = useState('');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    api
      .seats()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load seat health: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Reading the seat registry…</div>;
  if (!data.ok) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitamber/40 bg-pitamber/10 p-4 text-sm text-pitamber">
          Seat registry unavailable: {data.error ?? 'unknown'}
        </div>
      </div>
    );
  }

  const t = data.totals;
  const unread = data.seats.filter((s) => s.unread > 0);
  const rest = data.seats.filter((s) => s.unread === 0);
  const shown = showAll ? rest : rest.slice(0, 8);

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">Org</h1>
        <p className="text-sm text-pitfaint">
          Seats coordinate by posting flags into their own state file. A flag posted to a
          seat that has gone quiet is mail nobody opens.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-pitborder bg-pitsunk/60 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-pittext">{t.seats}</div>
          <div className="text-[11px] uppercase tracking-wider text-pitfaint">Seats</div>
        </div>
        <div className="rounded-lg border border-pitborder bg-pitsunk/60 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-pitamber">{t.cold}</div>
          <div className="text-[11px] uppercase tracking-wider text-pitfaint">
            Quiet &gt; {data.cold_days}d
          </div>
        </div>
        <div
          className={`rounded-lg border px-4 py-3 ${
            t.unread_flags > 0 ? 'border-pitred/40 bg-pitred/10' : 'border-pitborder bg-pitsunk/60'
          }`}
        >
          <div
            className={`text-2xl font-semibold tabular-nums ${
              t.unread_flags > 0 ? 'text-pitred' : 'text-pittext'
            }`}
          >
            {t.unread_flags}
          </div>
          <div className="text-[11px] uppercase tracking-wider text-pitfaint">Unread flags</div>
        </div>
        <div className="rounded-lg border border-pitborder bg-pitsunk/60 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-pittext">{t.open_flags}</div>
          <div className="text-[11px] uppercase tracking-wider text-pitfaint">Open flags</div>
        </div>
      </div>

      {unread.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-pitred">
            Unread mail — someone asked, this seat went quiet
          </h2>
          <div className="overflow-hidden rounded-lg border border-pitred/40">
            {unread.map((s) => (
              <div
                key={s.seat}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-pitred/40 px-4 py-3 last:border-b-0"
              >
                <span className="min-w-[10rem] text-sm text-pittext">{s.seat}</span>
                <span className="rounded border border-pitred/40 px-1.5 py-0.5 text-[11px] font-medium text-pitred">
                  {s.unread} waiting
                </span>
                <span className="text-[11px] text-pitfaint">silent {ageLabel(s.days_since_activity)}</span>
                {s.flags_posted_open > 0 && (
                  <span className="text-[11px] text-pitfaint">
                    · {s.flags_posted_open} of its own asks still open
                  </span>
                )}
                <span className="ml-auto font-mono text-[10px] text-pitfaint">{s.owned_file}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-pitfaint">All seats</h2>
        <div className="overflow-x-auto rounded-lg border border-pitborder">
          <table className="w-full text-left text-xs">
            <thead className="bg-pitsunk/60 text-pitfaint">
              <tr>
                <th className="px-3 py-2 font-medium">seat</th>
                <th className="px-3 py-2 font-medium">last active</th>
                <th className="px-3 py-2 font-medium">waiting on it</th>
                <th className="px-3 py-2 font-medium">its open asks</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.seat} className="border-t border-pitborder hover:bg-pittext/[0.03]">
                  <td className="px-3 py-2 text-pittext">
                    {s.seat}
                    {s.cold && <span className="ml-2 text-[10px] uppercase text-pitamber">quiet</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-pitfaint">
                    {ageLabel(s.days_since_activity)}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-pitmuted">{s.flags_waiting || '—'}</td>
                  <td className="px-3 py-2 tabular-nums text-pitmuted">
                    {s.flags_posted_open || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rest.length > 8 && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="text-xs text-pitfaint underline-offset-2 hover:text-pitmuted hover:underline"
          >
            {showAll ? 'Show fewer' : `Show ${rest.length - 8} more`}
          </button>
        )}
      </section>

      {t.unrouted_flags > 0 && (
        <p className="text-[11px] text-pitfaint">
          {t.unrouted_flags} flag target{t.unrouted_flags === 1 ? '' : 's'} could not be matched to a
          seat. Add the alias to seats.yaml so those route.
        </p>
      )}
    </motion.div>
  );
}
