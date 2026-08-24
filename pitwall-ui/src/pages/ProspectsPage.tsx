import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, ProspectFeed } from '../lib/api';

// The names behind "N prospects created today". A count cannot be checked; a list can.
// These rows come from autonomous sales agents, so being able to open them and see WHO
// was created is the difference between a metric and a verifiable fact.

const STATUS_TONE: Record<string, string> = {
  created: 'text-pitgreen border-pitgreen/40',
  duplicate_skipped: 'text-pitfaint border-pitborder',
  failed: 'text-pitred border-pitred/40',
};

export default function ProspectsPage() {
  const [days, setDays] = useState(1);
  const [data, setData] = useState<ProspectFeed | null>(null);
  const [error, setError] = useState('');
  const [onlyCreated, setOnlyCreated] = useState(true);

  useEffect(() => {
    setData(null);
    api
      .prospects(days)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [days]);

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load prospects: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Loading prospects…</div>;

  const rows = onlyCreated ? data.prospects.filter((p) => p.status === 'created') : data.prospects;
  const t = data.totals;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">Prospects created</h1>
        <p className="text-sm text-pitfaint">
          Who the sales agents actually put into the CRM. Open one to go looking for it in
          the right system.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {[1, 7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                days === d ? 'bg-pittext text-pitinvert' : 'border border-pitborder text-pitmuted hover:text-pittext'
              }`}
            >
              {d === 1 ? 'Today' : `${d}d`}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-pitmuted">
          <input
            type="checkbox"
            checked={onlyCreated}
            onChange={(e) => setOnlyCreated(e.target.checked)}
            className="accent-pitmuted"
          />
          created only
        </label>
        <div className="ml-auto flex gap-4 text-xs">
          <span className="text-pitgreen tabular-nums">{t.created} created</span>
          <span className="text-pitfaint tabular-nums">{t.duplicate_skipped} duplicate</span>
          {t.failed > 0 && <span className="text-pitred tabular-nums">{t.failed} failed</span>}
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-pitborder">
        <table className="w-full text-left text-xs">
          <thead className="bg-pitsunk/60 text-pitfaint">
            <tr>
              <th className="px-3 py-2 font-medium">prospect</th>
              <th className="px-3 py-2 font-medium">brand</th>
              <th className="px-3 py-2 font-medium">agent</th>
              <th className="px-3 py-2 font-medium">crm</th>
              <th className="px-3 py-2 font-medium">status</th>
              <th className="px-3 py-2 font-medium">when</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => (
              <tr key={i} className="border-t border-pitborder hover:bg-pittext/[0.03]">
                <td className="px-3 py-2 text-pittext">
                  {p.crm_link ? (
                    <a
                      href={p.crm_link}
                      target="_blank"
                      rel="noreferrer"
                      className="underline-offset-2 hover:underline"
                    >
                      {p.name}
                    </a>
                  ) : (
                    p.name
                  )}
                </td>
                <td className="px-3 py-2 text-pitmuted">{p.brand || '—'}</td>
                <td className="px-3 py-2 font-mono text-pitfaint">{p.agent}</td>
                <td className="px-3 py-2 uppercase text-pitfaint">{p.crm}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded border px-1.5 py-0.5 text-[10px] ${
                      STATUS_TONE[p.status] ?? 'text-pitfaint border-pitborder'
                    }`}
                  >
                    {p.status.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums text-pitfaint">{p.when.slice(0, 16)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-pitfaint">
                  Nothing in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] leading-relaxed text-pitfaint">{data.note}</p>
    </motion.div>
  );
}
