export type TeamSummary = {
  team_id: string;
  team_name: string;
  crm_provider: string;
  sprint_status: string;
  kpis: { open_opps: number; reply_rate: number; win_rate: number };
  agents: Array<{ agent_id: string; name: string; role: string; lane: string; status: string; last_run: string | null }>;
};

export type AxiomPanel = {
  last_run?: string | null;
  last_run_summary?: string;
  directives_issued_last_night?: number;
  directives_pending?: number;
  directives_picked_up?: number;
  directives_completed_today?: number;
  most_recent_directive?: {
    target: string;
    directive: string;
    priority: string;
    triggered_by: string;
  } | null;
};

export type CostPanel = {
  today_usd?: number;
  projection?: {
    daily_average?: number;
    projected_monthly?: number;
  };
  by_agent?: Array<{
    agent_name: string;
    total_input_tokens: number;
    total_output_tokens: number;
    total_cost_usd: number;
    total_runs: number;
  }>;
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
  axiom?: AxiomPanel;
  cost?: CostPanel;
};

export type OpsAgentHealth = {
  agent_id: string;
  name: string;
  role: string;
  last_run: string | null;
  status: string;
  log_preview?: string;
};

export type OpsBusinessSummary = {
  agents_ran?: string[];
  agents_missed?: string[];
};

export type OpsBusinessMeta = {
  name: string;
  sales_agent: string;
  crm: string;
};

export type OpsReport = {
  generated_at?: string;
  agents_ran?: number;
  agents_missed?: number;
  alert_count?: number;
  priority_recommendation?: string;
  alerts?: Array<{ message?: string } | string>;
  business_summary?: Record<string, OpsBusinessSummary>;
};

export type PitWallOpsDashboard = {
  timestamp: string;
  refresh_seconds: number;
  agent_health: OpsAgentHealth[];
  crm_today: Record<string, { created?: number; duplicate_skipped?: number }>;
  crm_week: Record<string, { created?: number; duplicate_skipped?: number }>;
  coo_report: OpsReport | null;
  businesses: Record<string, OpsBusinessMeta>;
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

export type ClearAlertsResponse = {
  status: string;
  alerts_remaining: number;
  alerts: Array<{ message?: string } | string>;
  priority: string;
  regenerated_at: string;
};

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${url}`);
  }
  return (await response.json()) as T;
}

async function postJson<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${url}`);
  }
  return (await response.json()) as T;
}

export const api = {
  telemetry: () => fetchJson<PitWallTelemetry>('/api/pitwall/telemetry'),
  opsDashboard: () => fetchJson<PitWallOpsDashboard>('/api/pitwall/ops-dashboard'),
  team: (teamId: string) => fetchJson<TeamDetail>(`/api/pitwall/team/${teamId}`),
  agent: (teamId: string, agentId: string) => fetchJson<AgentDetail>(`/api/pitwall/team/${teamId}/agent/${agentId}`),
  agentLogs: (agentId: string) => fetchJson<AgentLogs>(`/logs/${agentId}/history?limit=20`),
  clearAlerts: () => postJson<ClearAlertsResponse>('/api/pitwall/clear-alerts'),
};
