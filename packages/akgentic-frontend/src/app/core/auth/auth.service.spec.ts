import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { AuthService } from './auth.service';
import { ConfigService } from '../config/config.service';

/**
 * Specs for {@link AuthService.loginWithApiKey} (Stories 1.8, 1.9).
 *
 * The network boundary is the global `fetch`, which is stubbed via a Jasmine
 * spy — no real server is contacted.
 *
 * REVERT with auth.service.ts once akgentic-infra-enterprise Story 10.10 lands:
 * while Enterprise still answers /auth/login/apikey with a 302, login ignores the
 * login-call body and confirms the session via a second /auth/me request — so the
 * success path issues TWO fetches and the resolved user comes from /auth/me. When
 * 10.10 ships (Enterprise returns 200 JSON), restore the single-call body-parse
 * specs: one fetch, user read from the login body, no /auth/me round-trip.
 */
describe('AuthService', () => {
  let service: AuthService;
  let fetchSpy: jasmine.Spy;

  const API = 'http://backend.test';

  function makeResponse(
    ok: boolean,
    body: unknown = null,
  ): Response {
    return {
      ok,
      json: () => Promise.resolve(body),
    } as unknown as Response;
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        AuthService,
        {
          provide: ConfigService,
          useValue: { api: API, hideLogin: false },
        },
      ],
    });
    service = TestBed.inject(AuthService);
    fetchSpy = spyOn(window, 'fetch');
  });

  // REVERT with auth.service.ts (Story 10.10): success path is a two-step flow
  // today — apikey-login call (302/200, body ignored) then /auth/me confirm.
  describe('loginWithApiKey — success path (AC #2, #4, #5)', () => {
    it('calls /auth/login/apikey with the key URL-encoded and credentials: include', async () => {
      fetchSpy.and.returnValues(
        Promise.resolve(makeResponse(true)), // login: cookie set, body ignored
        Promise.resolve(makeResponse(true, { user_id: 'u1', name: 'Op' })), // /auth/me
      );

      await firstValueFrom(service.loginWithApiKey('secret key/+&'));

      const loginCall = fetchSpy.calls.argsFor(0);
      expect(loginCall[0]).toBe(
        `${API}/auth/login/apikey?apikey=${encodeURIComponent('secret key/+&')}`,
      );
      expect((loginCall[1] as RequestInit).credentials).toBe('include');
    });

    it('confirms via /auth/me so currentUser$ reflects the resolved user', async () => {
      const resolvedUser = { user_id: 'u1', email: 'op@test', name: 'Operator' };
      fetchSpy.and.returnValues(
        Promise.resolve(makeResponse(true)), // login: body ignored
        Promise.resolve(makeResponse(true, resolvedUser)), // /auth/me: source of truth
      );

      const result = await firstValueFrom(service.loginWithApiKey('valid-key'));

      // Two requests: the apikey-login call, then the /auth/me confirm.
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect(fetchSpy.calls.argsFor(1)[0]).toBe(`${API}/auth/me`);
      expect(result).toEqual(resolvedUser);
      expect(service.currentUserValue).toEqual(resolvedUser);
    });
  });

  describe('loginWithApiKey — error path (AC #3)', () => {
    it('errors the observable on an HTTP 401 response', async () => {
      fetchSpy.and.returnValue(Promise.resolve(makeResponse(false)));

      await expectAsync(
        firstValueFrom(service.loginWithApiKey('bad-key')),
      ).toBeRejected();
    });

    it('does not establish a session on a 401 (currentUser stays anonymous)', async () => {
      fetchSpy.and.returnValue(Promise.resolve(makeResponse(false)));

      await expectAsync(
        firstValueFrom(service.loginWithApiKey('bad-key')),
      ).toBeRejected();

      // REVERT (Story 10.10): two calls today — the login call then the /auth/me
      // confirm, which returns 401 (no session) and errors the observable.
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect(service.currentUserValue.user_id).toBe('anonymous');
    });
  });

  describe('no client-side key persistence (AC #6)', () => {
    it('never writes the API key to localStorage or sessionStorage', async () => {
      const localSpy = spyOn(Storage.prototype, 'setItem').and.callThrough();
      fetchSpy.and.returnValue(
        Promise.resolve(makeResponse(true, { success: true, user: { user_id: 'u1', name: 'Op' } })),
      );

      await firstValueFrom(service.loginWithApiKey('top-secret-key'));

      const persistedKey = localSpy.calls
        .allArgs()
        .some((args) => String(args[1]).includes('top-secret-key'));
      expect(persistedKey).toBe(false);
    });

    it('exposes no key-storage methods — the removed stubs are gone', () => {
      // Regression guard for AC #6: the deprecated stubs were deleted.
      const s = service as unknown as Record<string, unknown>;
      expect(s['setApiKey']).toBeUndefined();
      expect(s['getApiKey']).toBeUndefined();
      expect(s['clearApiKey']).toBeUndefined();
      expect(s['getAuthHeaders']).toBeUndefined();
    });
  });
});
