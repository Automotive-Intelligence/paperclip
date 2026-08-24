import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, ProspectFeed } from '../lib/api';

// The names behind "N prospects created today". A count cannot be checked; a list can.
// These rows come from autonomous sales agents, so being able to open them and see WHO
// was created is the difference between a metric and a verifiable fact.

const STATUS_TONE: Record<string, string> = {
  created: 'text-emerald-400 border-emerald-900/60',
  duplicate_skipped: 'text-gray-500 border-gray-800',
  failed: 'text-red-400 border-red-900/60',
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
        <div className="rounded-lg border border-red-900/60 bg-red-950/20 p-4 text-sm text-red-300">
          Could not load prospects: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-gray-500">Loading prospects…</div>;

  const rows = onlyCreated ? data.prospects.filter((p) => p.status === 'created') : data.prospects;
  const t = data.totals;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-gray-100">Prospects created</h1>
        <p className="text-sm text-gray-500">
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
                days === d ? 'bg-gray-100 text-gray-900' : 'border border-gray-800 text-gray-400 hover:text-gray-200'
              }`}
            >
              {d === 1 ? 'Today' : `${d}d`}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-400">
          <input
            type="checkbox"
            checked={onlyCreated}
            onChange={(e) => setOnlyCreated(e.target.checked)}
            className="accent-gray-300"
          />
          created only
        </label>
        <div className="ml-auto flex gap-4 text-xs">
          <span className="text-emerald-400 tabular-nums">{t.created} created</span>
          <span className="text-gray-500 tabular-nums">{t.duplicate_skipped} duplicate</span>
          {t.failed > 0 && <span className="text-red-400 tabular-nums">{t.failed} failed</span>}
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-black/40 text-gray-500">
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
              <tr key={i} className="border-t border-gray-900 hover:bg-white/[0.02]">
                <td className="px-3 py-2 text-gray-200">
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
                <td className="px-3 py-2 text-gray-400">{p.brand || '—'}</td>
                <td className="px-3 py-2 font-mono text-gray-500">{p.agent}</td>
                <td className="px-3 py-2 uppercase text-gray-500">{p.crm}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded border px-1.5 py-0.5 text-[10px] ${
                      STATUS_TONE[p.status] ?? 'text-gray-500 border-gray-800'
                    }`}
                  >
                    {p.status.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums text-gray-600">{p.when.slice(0, 16)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-gray-500">
                  Nothing in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] leading-relaxed text-gray-600">{data.note}</p>
    </motion.div>
  );
}
