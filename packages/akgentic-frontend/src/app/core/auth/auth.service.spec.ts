import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { AuthService } from './auth.service';
import { ConfigService } from '../config/config.service';

/**
 * Specs for {@link AuthService.loginWithApiKey} (Stories 1.8, 1.9).
 *
 * The network boundary is the global `fetch`, which is stubbed via a Jasmine
 * spy — no real server is contacted. The backend returns a `200` JSON body
 * (`{"success": true, "user": {...}}`) on success; the body itself is the
 * success signal — no redirect is followed and no `/auth/me` round-trip is
 * issued.
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

  describe('loginWithApiKey — success path (AC #2, #4, #5)', () => {
    it('calls /auth/login/apikey with the key URL-encoded and credentials: include', async () => {
      // One call only — the 200 JSON body is the success signal, no /auth/me.
      fetchSpy.and.returnValue(
        Promise.resolve(makeResponse(true, { success: true, user: { user_id: 'u1', name: 'Op' } })),
      );

      await firstValueFrom(service.loginWithApiKey('secret key/+&'));

      const loginCall = fetchSpy.calls.argsFor(0);
      expect(loginCall[0]).toBe(
        `${API}/auth/login/apikey?apikey=${encodeURIComponent('secret key/+&')}`,
      );
      expect((loginCall[1] as RequestInit).credentials).toBe('include');
    });

    it('reads the 200 JSON body so currentUser$ reflects the response user', async () => {
      const resolvedUser = { user_id: 'u1', email: 'op@test', name: 'Operator' };
      fetchSpy.and.returnValue(
        Promise.resolve(makeResponse(true, { success: true, user: resolvedUser })),
      );

      const result = await firstValueFrom(service.loginWithApiKey('valid-key'));

      // Single request — no followed redirect, no /auth/me round-trip.
      expect(fetchSpy).toHaveBeenCalledTimes(1);
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

      // No checkAuth() follow-up — only the failed login call was issued.
      expect(fetchSpy).toHaveBeenCalledTimes(1);
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
