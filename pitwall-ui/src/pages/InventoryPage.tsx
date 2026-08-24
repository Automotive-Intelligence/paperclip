import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { api, StackInventory } from '../lib/api';

// "What do we actually run?" — read off the live process, not anyone's memory.
// Built after three questions in one day about whether a piece of AVO existed got three
// different answers (never ours / ours but retired / running in production right now).
// The Decisions panel is the half a machine cannot derive, and it is what stops the
// same proposal being re-litigated a month later.

const SECTIONS = ['Jobs', 'Services', 'Routes', 'Dependencies', 'Decisions'] as const;
type Section = (typeof SECTIONS)[number];

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-pitborder bg-pitsunk/60 px-4 py-3">
      <div className="text-2xl font-semibold text-pittext tabular-nums">{value}</div>
      <div className="text-[11px] uppercase tracking-wider text-pitfaint">{label}</div>
    </div>
  );
}

function verdictColor(v: string): string {
  const s = v.toUpperCase();
  if (s.includes('RETIRED') || s.includes('DECOMMISSIONED')) return 'text-pitamber border-pitamber/40';
  if (s.includes('DISPROVEN') || s.includes('NOT OURS')) return 'text-pitred border-pitred/40';
  return 'text-pitmuted border-pitborder';
}

export default function InventoryPage() {
  const [data, setData] = useState<StackInventory | null>(null);
  const [error, setError] = useState('');
  const [section, setSection] = useState<Section>('Jobs');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    api
      .inventory()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const q = filter.trim().toLowerCase();
  const jobs = useMemo(
    () => (data?.jobs ?? []).filter((j) => !q || (j.id + j.name + j.trigger).toLowerCase().includes(q)),
    [data, q],
  );
  const services = useMemo(
    () => (data?.services ?? []).filter((s) => !q || (s.module + s.purpose).toLowerCase().includes(q)),
    [data, q],
  );
  const deps = useMemo(
    () => (data?.dependencies ?? []).filter((d) => !q || d.toLowerCase().includes(q)),
    [data, q],
  );

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load the inventory: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Reading the live system…</div>;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="p-6 space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">Stack Inventory</h1>
        <p className="text-sm text-pitfaint">
          What AVO actually runs, read off the live process. Check here before proposing,
          building, or declaring something missing.
        </p>
        <p className="text-[11px] text-pitfaint">
          Generated {data.generated} · {data.source}
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Scheduled jobs" value={data.counts.jobs} />
        <Stat label="Routes served" value={data.counts.routes} />
        <Stat label="Services" value={data.counts.services} />
        <Stat label="Dependencies" value={data.counts.dependencies} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {SECTIONS.map((s) => (
          <button
            key={s}
            onClick={() => setSection(s)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              section === s
                ? 'bg-pittext text-pitinvert'
                : 'border border-pitborder text-pitmuted hover:text-pittext'
            }`}
          >
            {s}
            {s === 'Decisions' && data.decisions.length > 0 && (
              <span className="ml-1.5 text-[10px] opacity-70">{data.decisions.length}</span>
            )}
          </button>
        ))}
        {section !== 'Decisions' && section !== 'Routes' && (
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter…"
            className="ml-auto rounded-md border border-pitborder bg-pitsunk/60 px-3 py-1.5 text-xs text-pittext placeholder:text-pitfaint focus:border-pitmuted focus:outline-none"
          />
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-pitborder">
        {section === 'Jobs' && (
          <table className="w-full text-left text-xs">
            <thead className="bg-pitsunk/60 text-pitfaint">
              <tr>
                <th className="px-3 py-2 font-medium">id</th>
                <th className="px-3 py-2 font-medium">name</th>
                <th className="px-3 py-2 font-medium">trigger</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="border-t border-pitborder hover:bg-pittext/[0.03]">
                  <td className="px-3 py-2 font-mono text-pitmuted">{j.id}</td>
                  <td className="px-3 py-2 text-pitmuted">{j.name}</td>
                  <td className="px-3 py-2 font-mono text-[11px] text-pitfaint">{j.trigger}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {section === 'Services' && (
          <table className="w-full text-left text-xs">
            <thead className="bg-pitsunk/60 text-pitfaint">
              <tr>
                <th className="px-3 py-2 font-medium">module</th>
                <th className="px-3 py-2 font-medium">purpose</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.module} className="border-t border-pitborder hover:bg-pittext/[0.03]">
                  <td className="px-3 py-2 font-mono text-pitmuted">{s.module}</td>
                  <td className="px-3 py-2 text-pitmuted">{s.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {section === 'Routes' && (
          <div className="flex flex-wrap gap-2 p-3">
            {data.route_groups.map((g) => (
              <div key={g.prefix} className="rounded-md border border-pitborder px-3 py-1.5 text-xs">
                <span className="font-mono text-pitmuted">{g.prefix}</span>
                <span className="ml-2 tabular-nums text-pitfaint">{g.count}</span>
              </div>
            ))}
          </div>
        )}

        {section === 'Dependencies' && (
          <div className="flex flex-wrap gap-2 p-3">
            {deps.map((d) => (
              <span key={d} className="rounded-md border border-pitborder px-2.5 py-1 font-mono text-[11px] text-pitmuted">
                {d}
              </span>
            ))}
          </div>
        )}

        {section === 'Decisions' && (
          <div className="divide-y divide-gray-900">
            <p className="px-3 py-2 text-[11px] text-pitfaint">
              Hand-written and preserved across regenerations. This is the part a machine
              cannot know: what we tried, killed, or ruled out, and why.
            </p>
            {data.decisions.length === 0 && (
              <p className="px-3 py-4 text-xs text-pitfaint">No decisions recorded yet.</p>
            )}
            {data.decisions.map((d, i) => (
              <div key={i} className="px-3 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm text-pittext">{d.thing}</span>
                  <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase ${verdictColor(d.verdict)}`}>
                    {d.verdict}
                  </span>
                  <span className="text-[11px] text-pitfaint">{d.when}</span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-pitfaint">{d.why}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}
