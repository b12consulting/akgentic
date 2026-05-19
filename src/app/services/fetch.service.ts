import { Injectable, inject } from '@angular/core';
import { MessageService } from 'primeng/api';
import { ConfigService } from './config.service';

/**
 * `responseType` controls how the response body is parsed.
 *
 * - `'json'` (default) — preserves the long-standing behaviour of calling
 *   `response.json()`. Every existing caller keeps working unchanged.
 * - `'text'` — calls `response.text()` instead. Needed for endpoints that
 *   return `application/yaml` (e.g. admin namespace export), on which
 *   `response.json()` would raise `SyntaxError`.
 *
 * Option A (single `fetch()` method with a new option) was chosen over a
 * sibling `fetchText()` method to avoid duplicating the error-notification,
 * `credentials: 'include'`, and 204/empty-body branches at two sites.
 */
export type FetchResponseType = 'json' | 'text';

/**
 * Error thrown by `FetchService.fetch` on non-OK HTTP responses (Story 11.3).
 *
 * `HttpError` exists so callers can branch on `.status` (e.g. 422 vs 5xx vs
 * 401) without sniffing the message string. The `body` field carries the
 * server response body — parsed as JSON when possible, falls back to the raw
 * text — so callers like the namespace-panel Save handler can consume a
 * `NamespaceValidationReport` on 422 without re-reading the response.
 *
 * The `.message` shape is preserved verbatim from the prior (non-throwing)
 * behaviour so existing notifications remain identical.
 */
export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = 'HttpError';
    // Restore prototype chain (TS target < ES6 / down-levelling safety).
    Object.setPrototypeOf(this, HttpError.prototype);
  }
}

/**
 * Best-effort JSON parse — returns parsed value on success, `undefined` on
 * failure. Used to populate `HttpError.body` with structured data when the
 * server emits JSON, and fall back to the raw string otherwise.
 */
function tryParseJson(text: string): unknown {
  if (text === '') {
    return undefined;
  }
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

@Injectable({
  providedIn: 'root',
})
export class FetchService {
  messageService: MessageService = inject(MessageService);
  private config = inject(ConfigService);

  /**
   * Issue a fetch, handle notifications, and parse the body.
   *
   * The error path (non-OK status), `credentials: 'include'` injection, and
   * 204/`content-length: 0` empty-body branch are identical for every
   * `responseType` — only the final body-parse call differs.
   *
   * On non-OK responses, the method throws an `HttpError` carrying the HTTP
   * status and response body. Callers that need to branch on the status
   * (e.g. the namespace-panel Save handler: 422 vs 401 vs other) can narrow
   * via `instanceof HttpError` or read `err.status`. Existing callers that
   * wrap the call in a try/catch on `Error` still receive an Error whose
   * `.message` matches the prior (non-throwing) notification text.
   *
   * @param responseType `'json'` (default) returns `response.json()`;
   *   `'text'` returns `response.text()` as a string.
   */
  async fetch({
    url,
    options,
    successMessage,
    errorMessage,
    responseType = 'json',
  }: {
    url: string;
    options?: RequestInit;
    successMessage?: string;
    errorMessage?: string;
    responseType?: FetchResponseType;
  }): Promise<any> {
    options = this.config.hideLogin
      ? options
      : { ...options, credentials: 'include' };

    let response: Response;
    try {
      response = await fetch(url, options);
    } catch {
      console.error('Network error: server unreachable');
      this.showNotification(
        errorMessage || 'Server unreachable. Check your connection.',
        'error'
      );
      return undefined;
    }

    if (!response.ok) {
      let errorDetail = '';
      try {
        const errorJson = await response.clone().json();
        errorDetail = errorJson.detail || '';
      } catch {
        // Response body is not valid JSON -- fall back to text
      }
      const errorBody = await response.text();
      console.error(
        `Error: ${response.status} - ${response.statusText}`,
        errorBody
      );

      const resolvedErrorMessage =
        errorMessage ||
        `Request failed: ${response.statusText}\n\n${errorDetail}`;

      this.showNotification(resolvedErrorMessage, 'error');

      // Throw an HttpError carrying the status + parsed body so callers that
      // need to branch (e.g. 422 vs 5xx) can inspect `.status` / `.body`.
      // Backwards compatibility: `.message` remains the same string that was
      // previously shown via the toast — existing Error-catching callers keep
      // receiving the same text.
      throw new HttpError(
        resolvedErrorMessage,
        response.status,
        tryParseJson(errorBody) ?? errorBody,
      );
    } else if (successMessage) {
      this.showNotification(successMessage, 'success');
    }

    // 204 No Content (and other bodyless responses) cannot be parsed as JSON
    if (response.status === 204 || response.headers.get('content-length') === '0') {
      return undefined;
    }

    return responseType === 'text' ? response.text() : response.json();
  }

  showNotification(
    message: string,
    type: 'success' | 'error' = 'success'
  ): void {
    this.messageService.add({
      severity: type,
      summary: message,
    });
  }
}
