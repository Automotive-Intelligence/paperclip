import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, ElevationFeed } from '../lib/api';

// The bar was always written down. What was missing was something that could say no,
// and somewhere the no would be seen. This page is the second half: a HOLD that only
// lives in a log is a HOLD nobody reads, which is how the last quality gate went
// sixty-three days cold without anyone noticing.

const VERDICT_TONE: Record<string, string> = {
  SHIP: 'text-pitgreen border-pitgreen/40',
  HOLD: 'text-pitamber border-pitamber/40',
  HOLD_UNREVIEWED: 'text-pitred border-pitred/40',
};

function ago(iso: string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso.slice(0, 16);
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`;
}

export default function ElevationPage() {
  const [data, setData] = useState<ElevationFeed | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<number | null>(null);

  const load = () =>
    api
      .elevation()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

  useEffect(() => {
    load();
  }, []);

  async function override(id: number) {
    setBusy(id);
    try {
      await api.clearElevationHold(id);
      await load();
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load the gate: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Reading the gate…</div>;

  const s = data.stats;
  const silent = s.last_run_age_seconds != null && s.last_run_age_seconds > 30 * 3600;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">Elevation gate</h1>
        <p className="text-sm text-pitfaint">
          What got held for being merely correct. The standard was always written down;
          this is the part that can say no.
        </p>
      </header>

      {/* If the gate itself is quiet, that is the headline, not the holds. */}
      {(s.last_run_age_seconds == null || silent) && (
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          {s.last_run_age_seconds == null
            ? 'The gate has never run. Nothing is checking whether work clears the bar.'
            : `The gate last ran ${Math.round(s.last_run_age_seconds / 3600)}h ago. Work is
               shipping unreviewed right now and looks the same as work that passed.`}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: 'reviewed', value: s.reviews },
          { label: 'held', value: s.holds },
          { label: 'open holds', value: s.open_holds },
          { label: 'hold rate', value: s.hold_rate == null ? '—' : `${Math.round(s.hold_rate * 100)}%` },
        ].map((m) => (
          <div key={m.label} className="rounded-lg border border-pitborder bg-pitsunk/60 p-3">
            <div className="text-[10px] uppercase tracking-wider text-pitfaint">{m.label}</div>
            <div className="text-xl font-semibold tabular-nums text-pittext">{m.value}</div>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="text-[10px] uppercase tracking-wider text-pitfaint">Open holds</div>
        {data.open_holds.length === 0 && (
          <p className="rounded-lg border border-pitborder bg-pitsunk/40 p-4 text-sm text-pitfaint">
            Nothing held. With a low review count that means untested, not clean.
          </p>
        )}
        {data.open_holds.map((h) => (
          <div key={h.id} className="rounded-lg border border-pitamber/40 bg-pitamber/10 p-4">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${VERDICT_TONE[h.verdict] ?? ''}`}>
                {h.verdict === 'HOLD_UNREVIEWED' ? 'not reviewed' : 'held'}
              </span>
              <span className="text-[10px] uppercase tracking-wider text-pitfaint">{h.kind}</span>
              <span className="text-[11px] text-pitfaint">{ago(h.when)}</span>
            </div>

            <h2 className="break-all text-[15px] font-semibold leading-snug text-pittext">{h.title}</h2>

            <ul className="mt-2 space-y-1">
              {h.reasons.map((r, i) => (
                <li key={i} className="text-xs leading-relaxed text-pitmuted">
                  <span className="text-pitamber">·</span> {r}
                </li>
              ))}
            </ul>

            {h.analysis?.strongest_version && (
              <div className="mt-3 rounded-md border border-pitborder bg-pitsunk/60 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-pitfaint">
                  The stronger version not being done
                </div>
                <div className="text-sm text-pittext">{h.analysis.strongest_version}</div>
                {h.analysis.why_not && (
                  <div className="mt-1 text-[11px] text-pitfaint">Left out: {h.analysis.why_not}</div>
                )}
              </div>
            )}

            <button
              type="button"
              onClick={() => override(h.id)}
              disabled={busy === h.id}
              className="mt-3 rounded-md border border-pitborder px-2.5 py-1 text-[11px] text-pitmuted hover:border-pitgreen/60 hover:text-pittext disabled:opacity-40"
            >
              {busy === h.id ? 'Overriding…' : 'Override and ship anyway'}
            </button>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-wider text-pitfaint">Recent verdicts</div>
        <div className="overflow-x-auto rounded-lg border border-pitborder">
          <table className="w-full text-left text-xs">
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id} className="border-t border-pitborder first:border-t-0">
                  <td className="px-3 py-2 w-24">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${VERDICT_TONE[r.verdict] ?? ''}`}>
                      {r.verdict === 'HOLD_UNREVIEWED' ? 'unreviewed' : r.verdict.toLowerCase()}
                    </span>
                  </td>
                  <td className="break-all px-3 py-2 text-pittext">{r.title}</td>
                  <td className="px-3 py-2 w-24 text-right text-pitfaint">{ago(r.when)}</td>
                </tr>
              ))}
              {data.recent.length === 0 && (
                <tr>
                  <td className="px-3 py-6 text-center text-pitfaint">No reviews yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </motion.div>
  );
}
