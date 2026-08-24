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
        <div className="rounded-lg border border-red-900/60 bg-red-950/20 p-4 text-sm text-red-300">
          Could not load seat health: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-gray-500">Reading the seat registry…</div>;
  if (!data.ok) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-amber-900/60 bg-amber-950/20 p-4 text-sm text-amber-300">
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
        <h1 className="text-xl font-semibold text-gray-100">Org</h1>
        <p className="text-sm text-gray-500">
          Seats coordinate by posting flags into their own state file. A flag posted to a
          seat that has gone quiet is mail nobody opens.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-gray-800 bg-black/30 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-gray-100">{t.seats}</div>
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Seats</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-black/30 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-amber-400">{t.cold}</div>
          <div className="text-[11px] uppercase tracking-wider text-gray-500">
            Quiet &gt; {data.cold_days}d
          </div>
        </div>
        <div
          className={`rounded-lg border px-4 py-3 ${
            t.unread_flags > 0 ? 'border-red-900/60 bg-red-950/20' : 'border-gray-800 bg-black/30'
          }`}
        >
          <div
            className={`text-2xl font-semibold tabular-nums ${
              t.unread_flags > 0 ? 'text-red-400' : 'text-gray-100'
            }`}
          >
            {t.unread_flags}
          </div>
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Unread flags</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-black/30 px-4 py-3">
          <div className="text-2xl font-semibold tabular-nums text-gray-100">{t.open_flags}</div>
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Open flags</div>
        </div>
      </div>

      {unread.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-red-400">
            Unread mail — someone asked, this seat went quiet
          </h2>
          <div className="overflow-hidden rounded-lg border border-red-900/40">
            {unread.map((s) => (
              <div
                key={s.seat}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-red-900/20 px-4 py-3 last:border-b-0"
              >
                <span className="min-w-[10rem] text-sm text-gray-100">{s.seat}</span>
                <span className="rounded border border-red-900/60 px-1.5 py-0.5 text-[11px] font-medium text-red-400">
                  {s.unread} waiting
                </span>
                <span className="text-[11px] text-gray-500">silent {ageLabel(s.days_since_activity)}</span>
                {s.flags_posted_open > 0 && (
                  <span className="text-[11px] text-gray-600">
                    · {s.flags_posted_open} of its own asks still open
                  </span>
                )}
                <span className="ml-auto font-mono text-[10px] text-gray-700">{s.owned_file}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">All seats</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-black/40 text-gray-500">
              <tr>
                <th className="px-3 py-2 font-medium">seat</th>
                <th className="px-3 py-2 font-medium">last active</th>
                <th className="px-3 py-2 font-medium">waiting on it</th>
                <th className="px-3 py-2 font-medium">its open asks</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((s) => (
                <tr key={s.seat} className="border-t border-gray-900 hover:bg-white/[0.02]">
                  <td className="px-3 py-2 text-gray-200">
                    {s.seat}
                    {s.cold && <span className="ml-2 text-[10px] uppercase text-amber-500">quiet</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-500">
                    {ageLabel(s.days_since_activity)}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-400">{s.flags_waiting || '—'}</td>
                  <td className="px-3 py-2 tabular-nums text-gray-400">
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
            className="text-xs text-gray-500 underline-offset-2 hover:text-gray-300 hover:underline"
          >
            {showAll ? 'Show fewer' : `Show ${rest.length - 8} more`}
          </button>
        )}
      </section>

      {t.unrouted_flags > 0 && (
        <p className="text-[11px] text-gray-600">
          {t.unrouted_flags} flag target{t.unrouted_flags === 1 ? '' : 's'} could not be matched to a
          seat. Add the alias to seats.yaml so those route.
        </p>
      )}
    </motion.div>
  );
}
