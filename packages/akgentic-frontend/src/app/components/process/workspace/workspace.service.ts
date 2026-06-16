import { inject, Injectable } from '@angular/core';
import { ConfigService } from '../../../core/config/config.service';
import { FetchService } from '../../../core/http/fetch.service';

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
  size?: number;
  modified?: string;
  extension?: string;
}

export interface FileContent {
  content: string | null;
  type: 'text' | 'binary';
  message?: string;
}

// Wire-level contract for the V2 backend endpoint
// GET /workspace/{team_id}/tree?path=<dir> — see ADR-006 §Decision 2.1.
// Internal use only: methods on WorkspaceService surface FileNode[], not
// WorkspaceTreeResponse — the wire type does not leak out of the service.
export interface WorkspaceFileEntry {
  name: string;
  is_dir: boolean;
  size: number;
}

export interface WorkspaceTreeResponse {
  team_id: string;
  path: string;
  entries: WorkspaceFileEntry[];
}

// Client-side upload pre-flight limit — mirrors the backend 10 MB cap
// (routes/workspace.py). Exported so Story 6-2's UploadModalComponent can
// surface the limit in its UI without duplicating the magic number.
export const MAX_UPLOAD_SIZE_BYTES = 10_485_760;

@Injectable({
  providedIn: 'root',
})
export class WorkspaceService {
  fetchService: FetchService = inject(FetchService);
  private config = inject(ConfigService);

  private get apiUrl(): string { return this.config.api; }

  /**
   * Append `workspace_id=<encoded>` to a URL when (and only when) a non-empty
   * id is supplied (Epic 23 / ADR-019). Centralises the append rule so call
   * sites never duplicate the concatenation. `undefined`/`null`/`''` append
   * NOTHING — the URL is returned byte-for-byte unchanged so the backend falls
   * back to `team_id`. The separator is `&` when the URL already carries a
   * query string (`?path=...`) and `?` for a bare endpoint (the upload POST).
   */
  private withWorkspaceId(url: string, workspaceId?: string | null): string {
    if (!workspaceId) return url;
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}workspace_id=${encodeURIComponent(workspaceId)}`;
  }

  async getWorkspaceTree(
    processId: string,
    path: string = '',
    workspaceId?: string
  ): Promise<FileNode[]> {
    const url = this.withWorkspaceId(
      `${this.apiUrl}/workspace/${processId}/tree?path=${encodeURIComponent(path)}`,
      workspaceId
    );
    const response = (await this.fetchService.fetch({
      url,
    })) as WorkspaceTreeResponse | undefined;

    if (!response || !Array.isArray(response.entries)) {
      throw new Error('Malformed workspace tree response: missing entries[]');
    }

    return response.entries.map((entry) => this.buildFileNode(entry, path));
  }

  private buildFileNode(entry: WorkspaceFileEntry, parentPath: string): FileNode {
    const nodePath = parentPath === '' ? entry.name : `${parentPath}/${entry.name}`;
    const dotIdx = entry.name.lastIndexOf('.');
    const extension =
      !entry.is_dir && dotIdx >= 0 ? entry.name.substring(dotIdx) : undefined;
    return {
      name: entry.name,
      path: nodePath,
      type: entry.is_dir ? 'directory' : 'file',
      size: entry.size,
      extension,
      // children: intentionally undefined — lazy, populated by a follow-up call
    };
  }

  async getFileContent(
    processId: string,
    filePath: string,
    workspaceId?: string
  ): Promise<FileContent> {
    // Bypass FetchService.fetch because its tail unconditionally calls
    // response.json() — incompatible with application/octet-stream bytes.
    // Inline the credentials logic from fetch.service.ts so auth cookies
    // still propagate. See ADR-006 §Decision 2.2.
    const url = this.withWorkspaceId(
      `${this.apiUrl}/workspace/${processId}/file?path=${encodeURIComponent(filePath)}`,
      workspaceId
    );
    const options: RequestInit = this.config.hideLogin
      ? {}
      : { credentials: 'include' };
    const response = await fetch(url, options);

    if (!response.ok) {
      throw new Error(response.statusText);
    }

    const buffer = await response.arrayBuffer();
    try {
      const decoder = new TextDecoder('utf-8', { fatal: true });
      const content = decoder.decode(buffer);
      return { content, type: 'text' };
    } catch {
      return {
        content: null,
        type: 'binary',
        message: 'Binary file cannot be displayed',
      };
    }
  }

  getDownloadUrl(
    processId: string,
    filePath: string,
    workspaceId?: string
  ): string {
    return this.withWorkspaceId(
      `${this.apiUrl}/workspace/${processId}/file?path=${encodeURIComponent(filePath)}`,
      workspaceId
    );
  }

  async uploadFiles(
    processId: string,
    files: File[],
    targetPath?: string,
    workspaceId?: string
  ): Promise<void> {
    // Pre-flight size check — reject the whole batch if any file is too large,
    // BEFORE issuing any HTTP request (AC7). Strict `>`: 10_485_760 is allowed.
    for (const file of files) {
      if (file.size > MAX_UPLOAD_SIZE_BYTES) {
        const message = `File "${file.name}" exceeds the 10 MB workspace upload limit`;
        this.fetchService.showNotification(message, 'error');
        throw new Error(message);
      }
    }

    const effectiveTarget = (targetPath ?? '').trim();
    // Sequential per-file POST loop — halt-on-error (AC6, NFR2).
    for (const file of files) {
      const fd = new FormData();
      const uploadPath =
        effectiveTarget === '' ? file.name : `${effectiveTarget}/${file.name}`;
      fd.append('path', uploadPath);
      fd.append('file', file);

      // The POST URL is a bare `/file` (path lives in the FormData body), so
      // `workspace_id` is the FIRST query param here — the helper appends with
      // `?`, not `&`. Absent id ⇒ unchanged `.../file` (AC4).
      await this.fetchService.fetch({
        url: this.withWorkspaceId(
          `${this.apiUrl}/workspace/${processId}/file`,
          workspaceId
        ),
        options: {
          method: 'POST',
          body: fd,
        },
      });
    }
  }
}
