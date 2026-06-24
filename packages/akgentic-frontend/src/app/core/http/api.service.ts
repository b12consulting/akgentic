import { inject, Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { AuthService } from '../auth/auth.service';
import { ConfigService } from '../config/config.service';
import { FetchService } from './fetch.service';
import {
  TeamContext,
  TeamPage,
  TeamResponse,
  TeamListResponse,
  EventResponse,
  EventListResponse,
  AgentStateResponse,
  AgentStateListResponse,
  toTeamContext,
} from '../context/team.interface';
import {
  Entry,
  NamespaceSummary,
  NamespaceValidationReport,
} from '../../protocol/catalog.interface';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  fetchService: FetchService = inject(FetchService);
  authService: AuthService = inject(AuthService);
  router: Router = inject(Router);
  private config = inject(ConfigService);

  private get apiUrl(): string { return this.config.api; }

  // --- Team CRUD (AC1) ---

  async getTeams(): Promise<TeamContext[]> {
    const response: TeamListResponse = await this.fetchService.fetch({
      url: `${this.apiUrl}/teams`,
    });
    const teams = response?.teams ?? [];
    return teams.map(toTeamContext);
  }

  /**
   * Classic offset+total page of teams (Epic 28). Issues `GET /teams?page&size`
   * — bare `/teams` when both args are omitted (server applies its defaults:
   * page 1 / size 250). A provided arg is appended even if it equals the
   * server default. Maps `teams` via `toTeamContext` and carries `total_count`
   * through; a missing/empty body yields `teams: []`, `total_count: 0`.
   */
  async getTeamsPage(page?: number, size?: number): Promise<TeamPage> {
    const params = new URLSearchParams();
    if (page !== undefined) {
      params.set('page', String(page));
    }
    if (size !== undefined) {
      params.set('size', String(size));
    }
    const query = params.toString();
    const url = query ? `${this.apiUrl}/teams?${query}` : `${this.apiUrl}/teams`;

    const response: TeamListResponse = await this.fetchService.fetch({ url });
    const teams = (response?.teams ?? []).map(toTeamContext);
    return { teams, total_count: response?.total_count ?? 0 };
  }

  async getTeam(teamId: string): Promise<TeamContext> {
    const response: TeamResponse = await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}`,
    });
    return toTeamContext(response);
  }

  async createTeam(namespace: string): Promise<TeamResponse> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/teams`,
      options: {
        method: 'POST',
        body: JSON.stringify({ catalog_namespace: namespace, params: {} }),
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  async deleteTeam(teamId: string): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}`,
      options: { method: 'DELETE' },
      successMessage: 'Team deleted successfully',
    });
  }

  async stopTeam(teamId: string): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/stop`,
      options: { method: 'POST' },
      successMessage: 'Team stopped successfully',
    });
  }

  async restoreTeam(teamId: string): Promise<TeamResponse> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/restore`,
      options: { method: 'POST' },
      successMessage: 'Team restored successfully',
    });
  }

  // --- Messaging (AC2) ---

  async sendMessage(
    teamId: string,
    content: string,
    agentName?: string | null
  ): Promise<void> {
    if (agentName) {
      return this.sendMessageTo(teamId, content, agentName);
    }
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/message`,
      options: {
        method: 'POST',
        body: JSON.stringify({ content }),
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  async sendMessageTo(
    teamId: string,
    content: string,
    agentName: string
  ): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/message/${agentName}`,
      options: {
        method: 'POST',
        body: JSON.stringify({ content }),
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  async sendMessageFromTo(
    teamId: string,
    senderName: string,
    recipientName: string,
    content: string
  ): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/message/from/${senderName}/to/${recipientName}`,
      options: {
        method: 'POST',
        body: JSON.stringify({ content }),
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  /** V2 processHumanInput: sends human input for a specific message in a team. */
  async processHumanInput(
    teamId: string,
    content: string,
    messageId: string
  ): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/human-input`,
      options: {
        method: 'POST',
        body: JSON.stringify({ content, message_id: messageId }),
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  // --- Events (AC3) ---

  async getEvents(teamId: string): Promise<EventResponse[]> {
    const response: EventListResponse = await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/events`,
    });
    return response?.events ?? [];
  }

  // --- Agent states (ADR-020 §2) ---

  /**
   * Per-agent state snapshots for a team — the read-path that seeds the
   * `state` store on init so the backstory head-block renders for STOPPED
   * teams (the durable event log carries no `StateChangedMessage`, ADR-013).
   * Mirrors `getEvents`: hits `GET /teams/{teamId}/agent-states` and unwraps
   * the `states` list, defaulting to `[]` when the body is absent/empty.
   * Each item's `agent_id` is the agent UUID (team Epic 23), so the caller
   * can key the `state` store directly with no name→UUID resolution.
   */
  async getAgentStates(teamId: string): Promise<AgentStateResponse[]> {
    const response: AgentStateListResponse = await this.fetchService.fetch({
      url: `${this.apiUrl}/teams/${teamId}/agent-states`,
    });
    return response?.states ?? [];
  }

  // --- Catalog ---

  /**
   * List catalog namespaces (flat summary) — powers the home-screen team
   * creation dropdown. Consumes catalog Story 16.6's `GET /catalog/namespaces`
   * endpoint, which returns `NamespaceSummary[]` directly (always a list,
   * even when empty).
   *
   * The optional `all` flag appends `?all=true`, the admin-only "see all"
   * lever: it surfaces every tenant's namespaces (not just owner+public).
   * `all=true` is honoured server-side ONLY for callers whose roles include
   * `admin`; a non-admin (or anonymous) caller sending it is silently treated
   * as the normal owner+public list (no error, no privilege grant). The flag
   * is therefore a convenience surface, not the authorization boundary — the
   * infra unscoping of admin reads is the boundary.
   */
  async getNamespaces(opts?: { all?: boolean }): Promise<NamespaceSummary[]> {
    const url = opts?.all
      ? `${this.apiUrl}/admin/catalog/namespaces?all=true`
      : `${this.apiUrl}/admin/catalog/namespaces`;
    return await this.fetchService.fetch({ url });
  }

  /**
   * Export a catalog namespace as raw YAML text.
   *
   * Hits `GET /admin/catalog/namespace/{namespace}/export` which returns
   * `application/yaml` — the response is consumed as text (not JSON).
   *
   * The optional `all` flag appends `?all=true` so an admin can open a
   * foreign-owned namespace surfaced by the home "show all" list. As with
   * `getNamespaces`, `all=true` is honoured server-side only for admins (the
   * `/admin/catalog/*` mount unscopes admin GETs); a non-admin sending it gets
   * the normal owner-scoped read. It widens reads only — never writes.
   */
  async exportNamespace(
    namespace: string,
    opts?: { all?: boolean },
  ): Promise<string> {
    const base = `${this.apiUrl}/admin/catalog/namespace/${namespace}/export`;
    const url = opts?.all ? `${base}?all=true` : base;
    return await this.fetchService.fetch({
      url,
      responseType: 'text',
    });
  }

  /**
   * Import (persist) a catalog namespace from YAML text.
   *
   * Hits `POST /admin/catalog/namespace/import`. The YAML is sent verbatim
   * as the request body with `Content-Type: application/yaml` — it is NOT
   * JSON-stringified or wrapped in any envelope. The response is parsed as
   * `Entry[]`.
   */
  async importNamespace(yaml: string): Promise<Entry[]> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespace/import`,
      options: {
        method: 'POST',
        body: yaml,
        headers: { 'Content-Type': 'application/yaml' },
      },
    });
  }

  /**
   * Validate a persisted catalog namespace by name.
   *
   * Hits `GET /admin/catalog/namespace/{namespace}/validate` and returns
   * the structured `NamespaceValidationReport`.
   */
  async validatePersistedNamespace(
    namespace: string
  ): Promise<NamespaceValidationReport> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespace/${namespace}/validate`,
    });
  }

  /**
   * Validate an in-memory YAML buffer against catalog invariants without
   * persisting it.
   *
   * Hits `POST /admin/catalog/namespace/validate` with the YAML verbatim as
   * the request body and `Content-Type: application/yaml`. Returns a
   * `NamespaceValidationReport`.
   */
  async validateNamespaceBuffer(
    yaml: string
  ): Promise<NamespaceValidationReport> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespace/validate`,
      options: {
        method: 'POST',
        body: yaml,
        headers: { 'Content-Type': 'application/yaml' },
      },
    });
  }

  /**
   * Delete a catalog namespace and all its entries.
   *
   * Hits `DELETE /admin/catalog/namespace/{namespace}` (ADR-028 §Decision 5).
   * A `204` resolves with no body (FetchService returns `undefined` for
   * 204 / empty-body responses). NO `successMessage` is passed — the panel
   * owns the success toast / live-region announcement so the messaging stays
   * consistent with the Clone flow. Non-2xx responses reject with an
   * `HttpError` carrying `.status` / `.body` so the caller can branch on
   * `403` (not-authorized), `409`/`422` (inbound-reference blocker), etc.
   */
  async deleteNamespace(namespace: string): Promise<void> {
    await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespace/${namespace}`,
      options: { method: 'DELETE' },
    });
  }

  /** No-op stub: description editing is not available in V2. */
  async updateTeamDescription(
    _teamId: string,
    _description: string | null
  ): Promise<void> {
    console.warn('updateTeamDescription is not available in V2');
  }
}
