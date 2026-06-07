// Typed client for the ai-dev-swarm control-plane API. Same-origin fetch +
// SSE (the SPA is served by FastAPI, loopback-only). Mutating commands carry
// the optional bearer token injected into <meta name="api-token"> by the server.

export type ProjectState =
  | "queued"
  | "planning"
  | "awaiting_approval"
  | "building"
  | "replanning"
  | "integration"
  | "done"
  | "blocked"
  | "killed";

export type MilestoneState = "pending" | "building" | "done" | "failed";

export interface ProjectSpec {
  title: string;
  summary: string;
  rationale: string;
  stack: string[];
  tags: string[];
  score: number;
}

export interface Project {
  id: string;
  name: string;
  spec: ProjectSpec;
  state: ProjectState;
  github_repo: string | null;
  status_detail: string | null;
  is_paused: boolean;
  created_at: string;
  updated_at: string;
}

export interface DashboardCard {
  id: string;
  name: string;
  state: ProjectState;
  status_detail: string | null;
  github_repo: string | null;
  done: number;
  total: number;
  cost: number;
}

export interface Milestone {
  id: string;
  project_id: string;
  ordinal: number;
  title: string;
  spec: { title: string; description: string; acceptance_criteria: { description: string; verifier: string }[]; technical_note: string };
  state: MilestoneState;
  retry_count: number;
  commit_hash: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetailSpend {
  cost_so_far: number;
  done: number;
  total: number;
  projected_total: number | null;
}

export interface ProjectDetail {
  project: Project;
  milestones: Milestone[];
  spend: ProjectDetailSpend;
}

export interface SpendByRole {
  role: string;
  tokens: number;
  cost_usd: number;
}
export interface SpendByProject {
  project_id: string;
  name: string;
  tokens: number;
  cost_usd: number;
}
export interface DailyPoint {
  date: string;
  cost: number;
}
export interface SpendSummary {
  daily_tokens: number;
  daily_cost_usd: number;
  all_time_tokens: number;
  all_time_cost_usd: number;
  by_role: SpendByRole[];
  by_project: SpendByProject[];
  daily_series: DailyPoint[];
}

export interface CriticScores {
  depth_ambition: number;
  usefulness_niche: number;
  novelty: number;
  decomposability: number;
  buildability: number;
}
export interface IdeaEvaluation {
  id: number;
  round: number;
  title: string;
  summary: string;
  scores: CriticScores;
  total: number;
  novel: boolean;
  accepted: boolean;
  rejected_reason: string | null;
  project_id: string | null;
  created_at: string;
}

export interface Setting {
  key: string;
  label: string;
  group: string;
  kind: "int" | "float" | "bool" | "str" | "choice";
  value: number | boolean | string;
  restart_required: boolean;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  help: string;
}

export interface TranscriptEntry {
  id: string;
  topic: string;
  project_id: string | null;
  role: string;
  kind: string;
  text: string;
  extra: Record<string, unknown>;
  at: string;
}

export interface CommandResult {
  ok: boolean;
  intent: string;
  detail: string;
  requires_confirmation?: boolean;
}

const API_TOKEN =
  (document.querySelector('meta[name="api-token"]') as HTMLMetaElement | null)?.content || "";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  projects: () => getJSON<Project[]>("/api/projects"),
  dashboard: () => getJSON<{ projects: DashboardCard[] }>("/api/dashboard"),
  project: (id: string) => getJSON<ProjectDetail>(`/api/projects/${id}`),
  spend: () => getJSON<SpendSummary>("/api/spend"),
  ideas: () => getJSON<IdeaEvaluation[]>("/api/ideas"),
  settings: () => getJSON<Setting[]>("/api/settings"),
  boardroom: (id: string) => getJSON<TranscriptEntry[]>(`/api/boardroom/${id}`),
  transcript: (id: string, limit = 400) =>
    getJSON<TranscriptEntry[]>(`/api/transcript/${id}?limit=${limit}`),
};

export type Command = Record<string, unknown> & { intent: string };

export async function sendCommand(command: Command): Promise<CommandResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_TOKEN) headers["Authorization"] = `Bearer ${API_TOKEN}`;
  const res = await fetch("/api/commands", {
    method: "POST",
    headers,
    body: JSON.stringify(command),
  });
  const data = (await res.json().catch(() => ({}))) as Partial<CommandResult>;
  if (!res.ok) {
    return {
      ok: false,
      intent: command.intent,
      detail: data.detail ? String(data.detail) : `HTTP ${res.status}`,
    };
  }
  return {
    ok: data.ok ?? true,
    intent: data.intent ?? command.intent,
    detail: data.detail ? String(data.detail) : "",
    requires_confirmation: data.requires_confirmation,
  };
}
