import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { api, PitWallTelemetry } from '../lib/api';
import StatusDot from '../components/StatusDot';

export default function PitWallPage() {
  const [data, setData] = useState<PitWallTelemetry | null>(null);
  const [error, setError] = useState<string>('');

  async function load() {
    try {
      const next = await api.telemetry();
      setData(next);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load telemetry');
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, []);

  const pipeline = useMemo(() => data?.activation_pipeline, [data]);

  return (
    <motion.main initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <header className="mb-6 rounded-2xl border border-pitborder bg-pitcard/80 p-5 shadow-pit backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-wide text-pittext md:text-4xl">Pit Wall</h1>
            <p className="mt-1 text-sm text-pitmuted">Master telemetry command for all teams and agents.</p>
          </div>
          <div className="rounded-xl border border-green-900/70 bg-black/30 px-3 py-2 text-sm text-pitmuted">
            <div className="flex items-center gap-2">
              <StatusDot status={data?.railway.health_ok ? 'green' : 'amber'} />
              <span>Railway {data?.railway.environment || 'unknown'} / {data?.railway.service || 'paperclip'}</span>
            </div>
          </div>
        </div>
      </header>

      {error ? <div className="mb-4 rounded-lg border border-pitred/60 bg-pitred/10 p-3 text-sm text-pitred">{error}</div> : null}

      <section className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        {(data?.teams || []).map((team) => (
          <motion.article key={team.team_id} whileHover={{ y: -2 }} className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
            <Link to={`/team/${team.team_id}`} className="block">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-pittext">{team.team_name}</h2>
                <StatusDot status={team.sprint_status === 'active' ? 'green' : 'amber'} />
              </div>
              <p className="mt-1 text-sm text-pitmuted">CRM: {team.crm_provider.toUpperCase()}</p>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                <div className="rounded-lg border border-pitborder p-2"><div className="text-xs text-pitmuted">Open</div><div className="text-lg text-pittext">{team.kpis.open_opps}</div></div>
                <div className="rounded-lg border border-pitborder p-2"><div className="text-xs text-pitmuted">Reply%</div><div className="text-lg text-pittext">{team.kpis.reply_rate}</div></div>
                <div className="rounded-lg border border-pitborder p-2"><div className="text-xs text-pitmuted">Win%</div><div className="text-lg text-pittext">{team.kpis.win_rate}</div></div>
              </div>
            </Link>
            <div className="mt-3 flex flex-wrap gap-2">
              {team.agents.map((agent) => (
                <Link key={agent.agent_id} to={`/team/${team.team_id}/agent/${agent.agent_id}`} className="rounded-full border border-pitborder px-2 py-1 text-xs text-pitmuted hover:border-pitgreen/70 hover:text-pittext">
                  {agent.name}
                </Link>
              ))}
            </div>
          </motion.article>
        ))}
      </section>

      <section className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
        <h3 className="mb-3 text-lg font-semibold text-pittext">Activation Pipeline</h3>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Artifact Created" value={pipeline?.artifact_created} />
          <Tile label="Risk Gate" value={pipeline?.risk_gate} />
          <Tile label="Approval Queue" value={pipeline?.approval_queue} />
          <Tile label="Dispatch" value={pipeline?.dispatch} />
        </div>
      </section>
    </motion.main>
  );
}

function Tile({ label, value }: { label: string; value?: number }) {
  return (
    <div className="rounded-xl border border-pitborder bg-black/20 p-3">
      <div className="text-xs uppercase tracking-wider text-pitmuted">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-pittext">{typeof value === 'number' ? value : '-'}</div>
    </div>
  );
}
