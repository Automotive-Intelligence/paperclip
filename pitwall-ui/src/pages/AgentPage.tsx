import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { api, AgentDetail } from '../lib/api';
import StatusDot from '../components/StatusDot';

export default function AgentPage() {
  const { teamId = '', agentId = '' } = useParams();
  const [data, setData] = useState<AgentDetail | null>(null);
  const [error, setError] = useState<string>('');

  async function load() {
    try {
      const next = await api.agent(teamId, agentId);
      setData(next);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent telemetry');
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [teamId, agentId]);

  const status = data?.agent.status || 'amber';

  return (
    <motion.main initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto max-w-4xl px-4 py-6 md:px-6">
      <div className="mb-4">
        <Link to={`/team/${teamId}`} className="text-sm text-pitmuted hover:text-pittext">&larr; Back to Team Dashboard</Link>
      </div>

      {error ? <div className="mb-4 rounded-lg border border-pitred/60 bg-pitred/10 p-3 text-sm text-pitred">{error}</div> : null}

      <section className="mb-4 rounded-2xl border border-pitborder bg-pitcard p-5 shadow-pit">
        <div className="flex items-center gap-4">
          <motion.div
            animate={status === 'green' ? { boxShadow: ['0 0 0 0 rgba(0,255,135,0.55)', '0 0 0 14px rgba(0,255,135,0.0)'] } : undefined}
            transition={{ repeat: Infinity, duration: 1.8 }}
            className="relative flex h-16 w-16 items-center justify-center rounded-full border border-pitborder bg-black text-xl font-semibold text-pittext"
          >
            {data?.agent.initials || '--'}
          </motion.div>
          <div>
            <h1 className="text-2xl font-semibold text-pittext">{data?.agent.name || agentId}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-pitmuted">
              <span className="rounded-full border border-pitborder px-2 py-1">{data?.agent.role || '-'}</span>
              <span className="rounded-full border border-pitborder px-2 py-1">Lane: {data?.agent.lane || '-'}</span>
              <span className="inline-flex items-center gap-2 rounded-full border border-pitborder px-2 py-1"><StatusDot status={status} /> {status.toUpperCase()}</span>
              <span className="rounded-full border border-pitborder px-2 py-1">Last run: {data?.agent.last_run || 'N/A'}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4">
        <Panel title="Today's Focus" items={data?.focus || []} alert={false} />
        <Panel title="Immediate Next Actions" items={data?.next_actions || []} alert={false} />
        <Panel title="Risk Flags" items={data?.risk_flags || []} alert={true} />
      </section>
    </motion.main>
  );
}

function Panel({ title, items, alert }: { title: string; items: string[]; alert: boolean }) {
  return (
    <div className={`rounded-2xl border bg-pitcard p-4 shadow-pit ${alert ? 'border-pitred/60 shadow-[0_0_24px_rgba(255,51,102,0.15)]' : 'border-pitborder'}`}>
      <h3 className="mb-3 text-lg font-semibold text-pittext">{title}</h3>
      <ul className="space-y-2">
        {items.map((item, idx) => (
          <li key={`${title}-${idx}`} className={`rounded-lg border p-2 text-sm ${alert ? 'border-pitred/60 bg-pitred/10 text-pittext' : 'border-pitborder bg-black/20 text-pittext'}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
