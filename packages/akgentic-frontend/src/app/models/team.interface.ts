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

// Maps to Python TeamListResponse
export interface TeamListResponse {
  teams: TeamResponse[];
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

// Maps to Python CreateTeamRequest
export interface CreateTeamRequest {
  catalog_entry_id: string;
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
