export type TeamSummary = {
  team_id: string;
  team_name: string;
  crm_provider: string;
  sprint_status: string;
  kpis: { open_opps: number; reply_rate: number; win_rate: number };
  agents: Array<{ agent_id: string; name: string; role: string; lane: string; status: string; last_run: string | null }>;
};

export type PitWallTelemetry = {
  timestamp: string;
  refresh_seconds: number;
  railway: {
    project: string;
    environment: string;
    service: string;
    health_ok: boolean;
    public_domain: string;
    service_url: string;
    health?: Record<string, unknown>;
  };
  activation_pipeline: {
    artifact_created: number;
    risk_gate: number;
    approval_queue: number;
    dispatch: number;
  };
  teams: TeamSummary[];
};

export type TeamDetail = {
  timestamp: string;
  team_id: string;
  team_name: string;
  crm_provider: string;
  live_status: string;
  stage: string;
  sprint: string;
  kpis: { open_opps: number; reply_rate: number; win_rate: number };
  pipeline_activity: Array<Record<string, unknown>>;
  priorities: string[];
  bottlenecks: Array<{ level: string; message: string }>;
  crm_live_metrics: Record<string, unknown>;
  agents: Array<{ agent_id: string; name: string; role: string; lane: string; status: string; last_run: string | null }>;
};

export type AgentDetail = {
  timestamp: string;
  team_id: string;
  team_name: string;
  agent: {
    agent_id: string;
    name: string;
    role: string;
    lane: string;
    status: string;
    last_run: string | null;
    initials: string;
  };
  focus: string[];
  next_actions: string[];
  risk_flags: string[];
};

export type AgentLogEntry = {
  id: number;
  log_type: string;
  run_date: string;
  created_at: string;
  preview: string;
};

export type AgentLogs = {
  agent: string;
  runs: AgentLogEntry[];
};

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${url}`);
  }
  return (await response.json()) as T;
}

export const api = {
  telemetry: () => fetchJson<PitWallTelemetry>('/api/pitwall/telemetry'),
  team: (teamId: string) => fetchJson<TeamDetail>(`/api/pitwall/team/${teamId}`),
  agent: (teamId: string, agentId: string) => fetchJson<AgentDetail>(`/api/pitwall/team/${teamId}/agent/${agentId}`),
  agentLogs: (agentId: string) => fetchJson<AgentLogs>(`/logs/${agentId}/history?limit=20`),
};
