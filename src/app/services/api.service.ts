import { inject, Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { AuthService } from './auth.service';
import { ConfigService } from './config.service';
import { FetchService } from './fetch.service';
import {
  TeamContext,
  TeamResponse,
  TeamListResponse,
  EventResponse,
  EventListResponse,
  toTeamContext,
} from '../models/team.interface';
import {
  Entry,
  NamespaceSummary,
  NamespaceValidationReport,
} from '../models/catalog.interface';

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

  // --- Catalog ---

  /**
   * List catalog namespaces (flat summary) — powers the home-screen team
   * creation dropdown. Consumes catalog Story 16.6's `GET /catalog/namespaces`
   * endpoint, which returns `NamespaceSummary[]` directly (always a list,
   * even when empty).
   */
  async getNamespaces(): Promise<NamespaceSummary[]> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespaces`,
    });
  }

  /**
   * Export a catalog namespace as raw YAML text.
   *
   * Hits `GET /admin/catalog/namespace/{namespace}/export` which returns
   * `application/yaml` — the response is consumed as text (not JSON).
   */
  async exportNamespace(namespace: string): Promise<string> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/namespace/${namespace}/export`,
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

  /** No-op stub: description editing is not available in V2. */
  async updateTeamDescription(
    _teamId: string,
    _description: string | null
  ): Promise<void> {
    console.warn('updateTeamDescription is not available in V2');
  }
}
