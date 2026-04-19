import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { api, ChangelogFeed, ChangelogSelected } from '../lib/api';

export default function ChangelogSection() {
  const [feed, setFeed] = useState<ChangelogFeed | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');

  async function loadLatest() {
    try {
      const data = await api.changelog();
      setFeed(data);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load changelog');
    }
  }

  async function loadWeek(week: number, year: number) {
    try {
      const data = await api.changelog(week, year);
      setFeed(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    }
  }

  useEffect(() => {
    loadLatest();
    const timer = setInterval(loadLatest, 120000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false); }
    if (open) document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const selected = feed?.selected;

  return (
    <section className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-lg font-semibold text-pittext">Weekly Changelog</h3>
          <p className="text-xs text-pitmuted">Build-in-public recap · tap to view full report</p>
        </div>
        {selected ? (
          <div className="rounded-lg border border-pitborder bg-black/30 px-2 py-1 text-[10px] uppercase tracking-wider text-pitmuted">
            Week {selected.week} · {selected.date_range}
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-xl border border-pitred/40 bg-pitred/10 p-3 text-xs text-pitred">{error}</div>
      ) : !selected ? (
        <div className="rounded-xl border border-pitborder bg-black/20 p-4 text-xs text-pitmuted">
          No changelog reports yet. Generated every Friday 5pm CST.
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="block w-full rounded-xl border border-pitborder bg-black/20 p-4 text-left transition hover:border-cyan-400/50 hover:bg-black/30"
        >
          <div className="grid grid-cols-3 gap-2">
            <Big label="Commits" value={selected.dev.commits} />
            <Big label="Features" value={selected.dev.features_count} />
            <Big label="Bugs Fixed" value={selected.dev.bugs_count} />
          </div>
          <div className="mt-3 text-center text-[11px] uppercase tracking-wider text-cyan-300/80">
            Tap to view full report → navigate by week
          </div>
        </button>
      )}

      <AnimatePresence>
        {open && selected ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 overflow-y-auto bg-black/85 p-4 backdrop-blur-sm"
            onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
          >
            <motion.div
              initial={{ opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.98 }}
              transition={{ duration: 0.2 }}
              className="mx-auto max-w-[960px] rounded-2xl border border-pitborder bg-pitcard p-6 shadow-pit"
            >
              <ModalBody
                feed={feed}
                selected={selected}
                onSelect={loadWeek}
                onClose={() => setOpen(false)}
              />
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

function ModalBody({
  feed,
  selected,
  onSelect,
  onClose,
}: {
  feed: ChangelogFeed | null;
  selected: ChangelogSelected;
  onSelect: (week: number, year: number) => void;
  onClose: () => void;
}) {
  return (
    <>
      <header className="mb-4 flex items-start justify-between gap-3 border-b border-pitborder pb-4">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-300">AVO · Weekly Build Report</div>
          <h2 className="mt-1 text-4xl font-bold tracking-tight text-pittext">
            Week {selected.week}
            <span className="text-pitmuted"> · {selected.year}</span>
          </h2>
          <div className="mt-1 text-base font-semibold text-cyan-300">{selected.date_range}</div>
          <div className="mt-1 font-mono text-[11px] text-pitmuted">Generated {selected.generated}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-pitborder bg-black/30 px-3 py-1 text-lg leading-none text-pittext hover:border-pitred hover:text-pitred"
          aria-label="Close"
        >
          ×
        </button>
      </header>

      {feed && feed.weeks.length > 1 ? (
        <div className="mb-5 flex gap-2 overflow-x-auto border-b border-pitborder pb-3">
          {feed.weeks.slice(0, 16).map((w) => {
            const active = w.week === selected.week && w.year === selected.year;
            return (
              <button
                key={`${w.year}-${w.week}`}
                type="button"
                onClick={() => onSelect(w.week, w.year)}
                className={`flex min-w-[100px] flex-col rounded-lg border px-3 py-2 text-left transition ${
                  active
                    ? 'border-cyan-400 bg-cyan-400/15'
                    : 'border-pitborder bg-black/20 hover:border-cyan-400/60'
                }`}
              >
                <span className="text-xs font-bold text-pittext">Week {w.week}</span>
                <span className={`text-[10px] ${active ? 'text-cyan-300' : 'text-pitmuted'}`}>{w.date_range}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      {selected.story ? (
        <div className="mb-5 rounded-xl border border-cyan-400/30 bg-cyan-400/5 p-4">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.15em] text-cyan-300">The Story This Week</div>
          <div className="whitespace-pre-line text-sm leading-relaxed text-pittext">{selected.story}</div>
        </div>
      ) : null}

      <div className="mb-5 grid grid-cols-3 gap-3">
        <BigStat label="Commits" value={selected.dev.commits} />
        <BigStat label="Features" value={selected.dev.features_count} />
        <BigStat label="Bugs Fixed" value={selected.dev.bugs_count} />
      </div>

      {selected.rivers.length ? (
        <Block title="Pipeline Activity">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {selected.rivers.map((r) => (
              <div key={r.name} className="rounded-xl border border-pitborder bg-black/20 p-3">
                <div className="text-sm font-bold text-pittext">{r.name}</div>
                <ul className="mt-2 space-y-1">
                  {r.items.map((item, i) => (
                    <li key={i} className="text-xs text-pitmuted">{item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Block>
      ) : null}

      <div className="mb-5 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Block title="Features Shipped" dense>
          <CommitList commits={selected.dev.features} empty="None this week." />
        </Block>
        <Block title="Bugs Fixed" dense>
          <CommitList commits={selected.dev.bugs} empty="None this week." />
        </Block>
      </div>

      <Block title="Cost Report">
        {selected.cost.length ? (
          <div className="rounded-xl border border-pitborder bg-black/20 p-3">
            {selected.cost.map((c, i) =>
              c.kind === 'heading' ? (
                <div key={i} className="mt-2 text-[10px] font-semibold uppercase tracking-wider text-cyan-300 first:mt-0">{c.text}</div>
              ) : (
                <div key={i} className="py-0.5 text-xs text-pitmuted">{c.text}</div>
              )
            )}
          </div>
        ) : (
          <div className="italic text-xs text-pitmuted">Cost tracking not yet active.</div>
        )}
      </Block>

      <Block title="Next Week">
        {selected.next_week.length ? (
          <ul className="rounded-xl border border-pitborder bg-black/20 p-4">
            {selected.next_week.map((item, i) => (
              <li key={i} className="relative border-b border-pitborder/60 py-2 pl-6 text-sm text-pittext last:border-none">
                <span className="absolute left-0 font-bold text-cyan-300">→</span>
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <div className="italic text-xs text-pitmuted">No priorities listed.</div>
        )}
      </Block>
    </>
  );
}

function Block({ title, children, dense = false }: { title: string; children: React.ReactNode; dense?: boolean }) {
  return (
    <div className={dense ? '' : 'mb-5'}>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-pitmuted">{title}</h3>
      {children}
    </div>
  );
}

function BigStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-pitborder bg-black/20 p-4 text-center">
      <div className="text-3xl font-extrabold text-cyan-300">{value}</div>
      <div className="mt-1 text-[10px] uppercase tracking-wider text-pitmuted">{label}</div>
    </div>
  );
}

function Big({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-black/30 p-3 text-center">
      <div className="text-2xl font-bold text-cyan-300">{value}</div>
      <div className="mt-1 text-[10px] uppercase tracking-wider text-pitmuted">{label}</div>
    </div>
  );
}

function CommitList({ commits, empty }: { commits: { sha: string; msg: string }[]; empty: string }) {
  if (!commits.length) return <div className="italic text-xs text-pitmuted">{empty}</div>;
  return (
    <ul className="rounded-xl border border-pitborder bg-black/20 px-3">
      {commits.map((c, i) => (
        <li key={i} className="flex items-baseline gap-2 border-b border-pitborder/60 py-2 text-xs leading-snug last:border-none">
          {c.sha ? (
            <span className="flex-shrink-0 rounded bg-cyan-400/15 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
              {c.sha}
            </span>
          ) : null}
          <span className="text-pittext">{c.msg}</span>
        </li>
      ))}
    </ul>
  );
}
