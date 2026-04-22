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

  async createTeam(catalogEntryId: string): Promise<TeamResponse> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/teams`,
      options: {
        method: 'POST',
        body: JSON.stringify({ catalog_entry_id: catalogEntryId }),
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

  // --- Catalog (AC4) ---

  async getTeamConfigs(): Promise<any> {
    return await this.fetchService.fetch({
      url: `${this.apiUrl}/admin/catalog/teams`,
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
