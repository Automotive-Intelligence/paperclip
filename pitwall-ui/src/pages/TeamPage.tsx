import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, TeamDetail } from '../lib/api';
import StatusDot from '../components/StatusDot';
import StatCard from '../components/StatCard';

export default function TeamPage() {
  const { teamId = '' } = useParams();
  const [data, setData] = useState<TeamDetail | null>(null);
  const [error, setError] = useState<string>('');

  async function load() {
    try {
      const next = await api.team(teamId);
      setData(next);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load team telemetry');
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [teamId]);

  const chartData = useMemo(() => (data?.pipeline_activity || []).map((row: Record<string, unknown>) => ({
    date: String(row.date || row.day || ''),
    prospects: Number(row.prospect_created || 0),
    emails: Number(row.email_sent || 0),
    demos: Number(row.demo_booked || 0),
    closed: Number(row.deal_closed || 0),
  })), [data]);

  return (
    <motion.main initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <Link to="/" className="text-sm text-pitmuted hover:text-pittext">&larr; Back to Pit Wall</Link>
        <div className="flex items-center gap-2 text-xs text-pitmuted"><StatusDot status={data?.live_status === 'active' ? 'green' : 'amber'} /><span>Live</span></div>
      </div>

      {error ? <div className="mb-4 rounded-lg border border-pitred/60 bg-pitred/10 p-3 text-sm text-pitred">{error}</div> : null}

      <header className="mb-4 rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
        <h1 className="text-2xl font-semibold text-pittext">{data?.team_name || teamId}</h1>
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-pitmuted">
          <span className="rounded-full border border-pitborder px-2 py-1">CRM: {String(data?.crm_provider || '').toUpperCase()}</span>
          <span className="rounded-full border border-pitborder px-2 py-1">Stage: {data?.stage || '-'}</span>
          <span className="rounded-full border border-pitborder px-2 py-1">Sprint: {data?.sprint || '-'}</span>
        </div>
      </header>

      <section className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <StatCard label="Open Opps" value={data?.kpis.open_opps || 0} />
        <StatCard label="Reply Rate" value={data?.kpis.reply_rate || 0} suffix="%" />
        <StatCard label="Win Rate" value={data?.kpis.win_rate || 0} suffix="%" />
      </section>

      <section className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Top Priorities" items={data?.priorities || []} tone="normal" />
        <Panel title="Current Bottlenecks" items={(data?.bottlenecks || []).map((b) => b.message)} tone="alert" />
      </section>

      <section className="mb-4 rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
        <h3 className="mb-3 text-lg font-semibold text-pittext">Pipeline Activity</h3>
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
              <XAxis dataKey="date" stroke="#8888aa" />
              <YAxis stroke="#8888aa" />
              <Tooltip contentStyle={{ background: '#12121a', border: '1px solid #1e1e2e' }} />
              <Bar dataKey="prospects" fill="#00ff87" radius={[4, 4, 0, 0]} />
              <Bar dataKey="emails" fill="#4aa8ff" radius={[4, 4, 0, 0]} />
              <Bar dataKey="demos" fill="#ffb800" radius={[4, 4, 0, 0]} />
              <Bar dataKey="closed" fill="#ff3366" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(data?.agents || []).map((agent) => (
          <Link key={agent.agent_id} to={`/team/${teamId}/agent/${agent.agent_id}`} className="rounded-xl border border-pitborder bg-pitcard p-3 shadow-pit hover:border-pitgreen/60">
            <div className="flex items-center justify-between">
              <div className="text-base font-semibold text-pittext">{agent.name}</div>
              <StatusDot status={agent.status} />
            </div>
            <div className="mt-1 text-sm text-pitmuted">{agent.role}</div>
          </Link>
        ))}
      </section>
    </motion.main>
  );
}

function Panel({ title, items, tone }: { title: string; items: string[]; tone: 'normal' | 'alert' }) {
  return (
    <div className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
      <h3 className="mb-3 text-lg font-semibold text-pittext">{title}</h3>
      <ul className="space-y-2">
        {items.map((item, idx) => (
          <li key={`${title}-${idx}`} className={`rounded-lg border p-2 text-sm ${tone === 'alert' ? 'border-pitamber/60 bg-pitamber/10 text-pittext' : 'border-pitborder bg-black/20 text-pittext'}`}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
