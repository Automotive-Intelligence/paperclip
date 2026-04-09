import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { api, PitWallOpsDashboard, PitWallTelemetry } from '../lib/api';
import StatusDot from '../components/StatusDot';

export default function PitWallPage() {
  const [data, setData] = useState<PitWallTelemetry | null>(null);
  const [opsData, setOpsData] = useState<PitWallOpsDashboard | null>(null);
  const [error, setError] = useState<string>('');

  async function load() {
    try {
      const [telemetry, ops] = await Promise.all([api.telemetry(), api.opsDashboard()]);
      setData(telemetry);
      setOpsData(ops);
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

  const teamCards = data?.teams || [];
  const opsAgentHealth = opsData?.agent_health || [];
  const alertItems = normalizeAlerts(opsData?.coo_report?.alerts);
  const totalProspectsToday = sumCreatedValues(opsData?.crm_today);
  const totalProspectsWeek = sumCreatedValues(opsData?.crm_week);
  const alertCount = opsData?.coo_report?.alert_count || 0;
  const pipeline = data?.activation_pipeline;

  return (
    <motion.main initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto max-w-7xl px-4 py-6 md:px-6">
      <header className="mb-6 rounded-2xl border border-pitborder bg-pitcard/80 p-5 shadow-pit backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-wide text-pittext md:text-4xl">Operations Pit Wall</h1>
            <p className="mt-1 text-sm text-pitmuted">Unified company dashboard for team telemetry, ops alerts, and agent execution.</p>
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

      <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricTile label="Agents Active" value={opsAgentHealth.length} accent="cyan" />
        <MetricTile label="Prospects Today" value={totalProspectsToday} accent="green" />
        <MetricTile label="This Week" value={totalProspectsWeek} accent="blue" />
        <MetricTile label="Alerts" value={alertCount} accent={alertCount > 0 ? 'amber' : 'green'} />
      </section>

      {alertItems.length ? (
        <section className="mb-6 space-y-2">
          {alertItems.map((alert, index) => (
            <div key={`${alert}-${index}`} className="rounded-xl border border-pitamber/40 bg-pitamber/10 px-4 py-3 text-sm text-pittext">
              {alert}
            </div>
          ))}
        </section>
      ) : null}

      <section className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        {teamCards.map((team) => (
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

      <section className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
          <div className="mb-4 flex items-center justify-between gap-2">
            <h3 className="text-lg font-semibold text-pittext">Business Ops Board</h3>
            <div className="text-xs text-pitmuted">Legacy dashboard feed, merged here</div>
          </div>
          <div className="space-y-3">
            {teamCards.map((team) => {
              const businessMeta = opsData?.businesses?.[team.team_id];
              const businessSummary = opsData?.coo_report?.business_summary?.[team.team_id];
              const salesAgentId = businessMeta?.sales_agent || '';
              const todayCreated = opsData?.crm_today?.[salesAgentId]?.created || 0;
              const weekCreated = opsData?.crm_week?.[salesAgentId]?.created || 0;
              const ranAgents = businessSummary?.agents_ran || [];
              const missedAgents = businessSummary?.agents_missed || [];

              return (
                <div key={`${team.team_id}-ops`} className="rounded-xl border border-pitborder bg-black/20 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-base font-semibold text-pittext">{businessMeta?.name || team.team_name}</div>
                      <div className="mt-1 text-xs uppercase tracking-wide text-pitmuted">{businessMeta?.crm || team.crm_provider}</div>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center text-xs">
                      <CompactValue label="Today" value={todayCreated} />
                      <CompactValue label="Week" value={weekCreated} />
                      <CompactValue label="Agents" value={ranAgents.length} />
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {ranAgents.map((agentId) => {
                      const agent = opsAgentHealth.find((item) => item.agent_id === agentId);
                      return (
                        <AgentChip
                          key={`${team.team_id}-${agentId}`}
                          name={agent?.name || agentId}
                          role={agent?.role || 'Agent'}
                          status={agent?.status || 'unknown'}
                        />
                      );
                    })}
                    {missedAgents.map((agentId) => (
                      <AgentChip key={`${team.team_id}-${agentId}-missed`} name={agentId} role="missed" status="red" muted />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3 className="text-lg font-semibold text-pittext">COO Command</h3>
            <div className="text-xs text-pitmuted">{formatTimestamp(opsData?.coo_report?.generated_at)}</div>
          </div>
          <div className="mb-4 grid grid-cols-3 gap-2">
            <CompactValue label="Ran" value={opsData?.coo_report?.agents_ran || 0} />
            <CompactValue label="Missed" value={opsData?.coo_report?.agents_missed || 0} />
            <CompactValue label="Alerts" value={alertCount} />
          </div>
          <div className="rounded-xl border border-pitborder bg-black/20 p-3 text-sm leading-6 text-pittext">
            {opsData?.coo_report?.priority_recommendation || 'No current COO recommendation.'}
          </div>
          <div className="mt-4 space-y-2">
            {opsAgentHealth.slice(0, 6).map((agent) => (
              <div key={agent.agent_id} className="flex items-start justify-between gap-3 rounded-xl border border-pitborder bg-black/20 px-3 py-2">
                <div>
                  <div className="text-sm font-medium text-pittext">{agent.name}</div>
                  <div className="text-xs text-pitmuted">{agent.role}</div>
                </div>
                <div className="text-right">
                  <div className="flex items-center justify-end gap-2 text-xs text-pitmuted">
                    <StatusDot status={agent.status} />
                    <span>{relativeTime(agent.last_run)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
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

function sumCreatedValues(values?: Record<string, { created?: number }>) {
  return Object.values(values || {}).reduce((sum, item) => sum + (item.created || 0), 0);
}

function normalizeAlerts(alerts?: Array<{ message?: string } | string>) {
  return (alerts || []).map((alert) => {
    if (typeof alert === 'string') {
      return alert;
    }
    return alert.message || 'Unknown alert';
  });
}

function relativeTime(value?: string | null) {
  if (!value) {
    return 'Never';
  }

  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return 'Unknown';
  }

  const diffMinutes = Math.round((Date.now() - timestamp) / 60000);
  if (diffMinutes < 1) {
    return 'Just now';
  }
  if (diffMinutes < 60) {
    return `${diffMinutes}m ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }
  return `${Math.round(diffHours / 24)}d ago`;
}

function formatTimestamp(value?: string) {
  if (!value) {
    return 'No report yet';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'No report yet';
  }
  return date.toLocaleString();
}

function MetricTile({ label, value, accent }: { label: string; value: number; accent: 'cyan' | 'green' | 'blue' | 'amber' }) {
  const accentClass = {
    cyan: 'text-cyan-300',
    green: 'text-pitgreen',
    blue: 'text-blue-300',
    amber: 'text-pitamber',
  }[accent];

  return (
    <div className="rounded-2xl border border-pitborder bg-pitcard p-4 shadow-pit">
      <div className="text-xs uppercase tracking-wide text-pitmuted">{label}</div>
      <div className={`mt-2 text-3xl font-semibold ${accentClass}`}>{value}</div>
    </div>
  );
}

function CompactValue({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-pitborder px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-pitmuted">{label}</div>
      <div className="mt-1 text-lg font-semibold text-pittext">{value}</div>
    </div>
  );
}

function AgentChip({ name, role, status, muted = false }: { name: string; role: string; status: string; muted?: boolean }) {
  return (
    <div className={`flex items-center gap-2 rounded-full border border-pitborder px-3 py-1.5 text-xs ${muted ? 'opacity-60' : ''}`}>
      <StatusDot status={status} />
      <span className="font-medium text-pittext">{name}</span>
      <span className="text-pitmuted">{role}</span>
    </div>
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
