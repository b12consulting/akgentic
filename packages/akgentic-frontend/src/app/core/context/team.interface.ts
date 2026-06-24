/**
 * V2 Team data model — replaces ProcessContext from V1.
 * Maps to Python TeamResponse, EventResponse from akgentic.infra.server.models.
 */

// Maps to Python TeamResponse (from akgentic.infra.server.models)
export interface TeamResponse {
  team_id: string;
  name: string;
  status: string;
  user_id: string;
  created_at: string;
  updated_at: string;
}

// Maps to Python TeamListResponse (classic offset+total pagination, Epic 28).
// `total_count` is the total teams the user owns across ALL pages; `teams` is
// the current page. No `next_cursor` (the parked cursor approach, ADR-031).
export interface TeamListResponse {
  teams: TeamResponse[];
  total_count: number;
}

// Maps to Python EventResponse
export interface EventResponse {
  team_id: string;
  sequence: number;
  event: any;
  timestamp: string;
}

// Maps to Python EventListResponse
export interface EventListResponse {
  events: EventResponse[];
}

// Maps to Python AgentStateResponse (akgentic.infra.server.models, Story 35-1).
// `agent_id` is the agent UUID (team Epic 23) — the exact key the per-agent
// `state` store uses, so no client-side name→UUID resolution is needed.
export interface AgentStateResponse {
  agent_id: string;
  name: string | null;
  state: Record<string, unknown>;
  updated_at: string;
}

// Maps to Python AgentStateListResponse
export interface AgentStateListResponse {
  states: AgentStateResponse[];
}

// Maps to Python CreateTeamRequest (akgentic.infra.server.models)
export interface CreateTeamRequest {
  catalog_namespace: string;
  params?: Record<string, string>;
}

// Maps to Python SendMessageRequest
export interface SendMessageRequest {
  content: string;
}

// Maps to Python HumanInputRequest
export interface HumanInputRequest {
  content: string;
  message_id: string;
}

/**
 * Frontend-facing team model — slimmed down from V1 ProcessContext.
 * Only includes fields actually used by frontend components.
 */
export interface TeamContext {
  team_id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  config_name: string;
  description?: string | null;
}

/**
 * Frontend-facing page of teams (classic offset+total pagination, Epic 28).
 * `teams` is already mapped to the `TeamContext` view model; `total_count`
 * is carried through verbatim from `TeamListResponse`.
 */
export interface TeamPage {
  teams: TeamContext[];
  total_count: number;
}

/** Check if a team is currently running. */
export function isRunning(team: TeamContext): boolean {
  return team.status === 'running';
}

/**
 * Map a V2 TeamResponse to the frontend TeamContext model.
 */
export function toTeamContext(response: TeamResponse): TeamContext {
  return {
    team_id: response.team_id,
    name: response.name,
    status: response.status,
    created_at: response.created_at,
    updated_at: response.updated_at,
    // config_name is not in TeamResponse -- V2 does not return it.
    // Use team name as placeholder; future story may populate from catalog metadata.
    config_name: response.name,
    description: null,
  };
}
