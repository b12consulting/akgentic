import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';

import { ConfigService } from './config.service';
import { FetchService, HttpError } from './fetch.service';

/** Build a minimal `Response`-like object usable by `FetchService.fetch`. */
function makeResponse({
  ok = true,
  status = 200,
  statusText = 'OK',
  contentLength = null,
  jsonValue,
  textValue,
  throwOnJson = false,
}: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  contentLength?: string | null;
  jsonValue?: unknown;
  textValue?: string;
  throwOnJson?: boolean;
}): Response {
  const headers = new Headers();
  if (contentLength !== null) {
    headers.set('content-length', contentLength);
  }
  const resp: Partial<Response> = {
    ok,
    status,
    statusText,
    headers,
    clone: function clone(): Response {
      return makeResponse({
        ok,
        status,
        statusText,
        contentLength,
        jsonValue,
        textValue,
        throwOnJson,
      });
    } as Response['clone'],
    json: async (): Promise<unknown> => {
      if (throwOnJson) {
        throw new SyntaxError('not JSON');
      }
      return jsonValue;
    },
    text: async (): Promise<string> => textValue ?? '',
  };
  return resp as Response;
}

describe('FetchService', () => {
  let service: FetchService;
  let messageServiceSpy: jasmine.SpyObj<MessageService>;
  let originalFetch: typeof fetch;

  beforeEach(() => {
    messageServiceSpy = jasmine.createSpyObj('MessageService', ['add']);

    TestBed.configureTestingModule({
      providers: [
        FetchService,
        { provide: MessageService, useValue: messageServiceSpy },
        {
          provide: ConfigService,
          useValue: { hideLogin: false } as Partial<ConfigService>,
        },
      ],
    });

    service = TestBed.inject(FetchService);
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe('responseType: default JSON behaviour (existing callers unaffected)', () => {
    it('calls response.json() when responseType is omitted', async () => {
      const payload = { foo: 'bar' };
      globalThis.fetch = jasmine
        .createSpy('fetch')
        .and.resolveTo(makeResponse({ jsonValue: payload }));

      const result = await service.fetch({ url: 'https://x/api' });

      expect(result).toEqual(payload);
    });

    it('calls response.json() when responseType is explicitly "json"', async () => {
      const payload = { a: 1 };
      globalThis.fetch = jasmine
        .createSpy('fetch')
        .and.resolveTo(makeResponse({ jsonValue: payload }));

      const result = await service.fetch({
        url: 'https://x/api',
        responseType: 'json',
      });

      expect(result).toEqual(payload);
    });
  });

  describe('responseType: "text"', () => {
    it('calls response.text() and returns the raw string, not JSON', async () => {
      const yamlText = 'namespace: foo\nname: My NS\n';
      // Response body would NOT parse as JSON — confirms we never call .json()
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          textValue: yamlText,
          throwOnJson: true,
        })
      );

      const result = await service.fetch({
        url: 'https://x/admin/catalog/namespace/foo/export',
        responseType: 'text',
      });

      expect(result).toBe(yamlText);
    });

    it('preserves the non-OK error path (notification fired) even with text body', async () => {
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          ok: false,
          status: 500,
          statusText: 'Internal Server Error',
          textValue: 'boom',
          throwOnJson: true,
        })
      );

      // Non-OK responses now throw HttpError (Story 11.3) — the toast still
      // fires as a side-effect before the throw.
      await expectAsync(
        service.fetch({
          url: 'https://x/admin/catalog/namespace/foo/export',
          responseType: 'text',
        })
      ).toBeRejected();

      expect(messageServiceSpy.add).toHaveBeenCalledTimes(1);
      const call = messageServiceSpy.add.calls.first().args[0];
      expect(call.severity).toBe('error');
    });

    it('returns undefined on 204 No Content even in text mode', async () => {
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          status: 204,
          statusText: 'No Content',
          contentLength: '0',
          textValue: '',
        })
      );

      const result = await service.fetch({
        url: 'https://x/admin/catalog/namespace/foo/export',
        responseType: 'text',
      });

      expect(result).toBeUndefined();
    });
  });

  describe('credentials: include injection', () => {
    it('injects credentials: "include" when hideLogin is false (default behaviour preserved)', async () => {
      const fetchSpy = jasmine
        .createSpy('fetch')
        .and.resolveTo(makeResponse({ jsonValue: {} }));
      globalThis.fetch = fetchSpy;

      await service.fetch({ url: 'https://x/api' });

      const opts = fetchSpy.calls.first().args[1] as RequestInit;
      expect(opts.credentials).toBe('include');
    });
  });

  // --- Story 11.3 — HttpError thrown on non-OK ---------------------------

  describe('HttpError on non-OK (Story 11.3)', () => {
    it('throws an HttpError with status and parsed JSON body on 422', async () => {
      const errBody = {
        namespace: 'foo',
        ok: false,
        global_errors: ['bad'],
        entry_issues: [],
      };
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          ok: false,
          status: 422,
          statusText: 'Unprocessable Entity',
          jsonValue: errBody,
          textValue: JSON.stringify(errBody),
        }),
      );

      let caught: unknown = null;
      try {
        await service.fetch({ url: 'https://x/admin/catalog/namespace/import' });
      } catch (err) {
        caught = err;
      }

      expect(caught).toBeTruthy();
      expect(caught instanceof HttpError).toBeTrue();
      const httpErr = caught as HttpError;
      expect(httpErr.status).toBe(422);
      expect(httpErr.body).toEqual(errBody);
      expect(httpErr.name).toBe('HttpError');
      // `.message` must still match the existing notification text shape,
      // so existing Error-catching callers keep receiving the same string.
      expect(httpErr.message).toContain('Request failed');
    });

    it('throws HttpError on 500 with raw-text body when JSON parse fails', async () => {
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          ok: false,
          status: 500,
          statusText: 'Internal Server Error',
          textValue: 'boom',
          throwOnJson: true,
        }),
      );

      let caught: unknown = null;
      try {
        await service.fetch({ url: 'https://x/api' });
      } catch (err) {
        caught = err;
      }

      expect(caught instanceof HttpError).toBeTrue();
      const httpErr = caught as HttpError;
      expect(httpErr.status).toBe(500);
      expect(httpErr.body).toBe('boom');
    });

    it('HttpError preserves the existing `.message` shape for backwards-compat', async () => {
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          ok: false,
          status: 403,
          statusText: 'Forbidden',
          jsonValue: { detail: 'Nope' },
          textValue: JSON.stringify({ detail: 'Nope' }),
        }),
      );

      await expectAsync(service.fetch({ url: 'https://x/api' })).toBeRejected();

      // The surface the notification uses must still be the same shape
      // ("Request failed: ...\n\n<detail>") so existing callers' catch-on-
      // Error branches see the same message.
      const toastArgs = messageServiceSpy.add.calls.first().args[0];
      expect(toastArgs.summary as string).toContain('Request failed');
      expect(toastArgs.summary as string).toContain('Nope');
    });

    it('throws HttpError carrying status 401 without suppressing the toast', async () => {
      globalThis.fetch = jasmine.createSpy('fetch').and.resolveTo(
        makeResponse({
          ok: false,
          status: 401,
          statusText: 'Unauthorized',
          textValue: '',
          throwOnJson: true,
        }),
      );

      let caught: unknown = null;
      try {
        await service.fetch({ url: 'https://x/api' });
      } catch (err) {
        caught = err;
      }
      expect(caught instanceof HttpError).toBeTrue();
      expect((caught as HttpError).status).toBe(401);
      // Existing toast-on-error behaviour preserved — the panel's save
      // handler relies on the global toast fired here for 401 fall-through.
      expect(messageServiceSpy.add).toHaveBeenCalledTimes(1);
    });
  });
});
