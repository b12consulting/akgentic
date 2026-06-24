import { inject, Injectable } from '@angular/core';
import { BehaviorSubject, from, Observable, of } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';
import { ConfigService } from '../config/config.service';

const ANONYMOUS_USER = { user_id: 'anonymous', email: '', name: 'Anonymous' };

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private config = inject(ConfigService);
  private currentUserSubject = new BehaviorSubject<any>(ANONYMOUS_USER);
  currentUser$ = this.currentUserSubject.asObservable();

  /**
   * Check auth status against the backend.
   * - Community tier (hideLogin=true): returns anonymous immediately.
   * - Enterprise tier: calls GET /auth/me with session cookie.
   */
  checkAuth(): Observable<any> {
    if (this.config.hideLogin) {
      return of(ANONYMOUS_USER);
    }

    const options: RequestInit = { credentials: 'include' };
    return from(
      fetch(`${this.config.api}/auth/me`, options).then(async (r) => {
        if (!r.ok) return null;
        return r.json();
      })
    ).pipe(
      tap((user) => {
        if (user) {
          // Ensure name is never empty — fall back to email or user_id
          if (!user.name) {
            user.name = user.email || user.user_id || 'User';
          }
          this.currentUserSubject.next(user);
        } else {
          this.currentUserSubject.next(ANONYMOUS_USER);
        }
      }),
      catchError(() => {
        this.currentUserSubject.next(ANONYMOUS_USER);
        return of(null);
      })
    );
  }

  get currentUserValue(): any {
    return this.currentUserSubject.value;
  }

  /**
   * Authenticate with an API key against the backend.
   *
   * Drives `GET /auth/login/apikey?apikey=<key>` with `credentials: 'include'`
   * so the backend's `Set-Cookie` session response is stored by the browser.
   * The API key is sent once as a query parameter and is NEVER persisted in the
   * browser; the session cookie set by the backend is the sole post-login
   * credential, exactly as on the OAuth path.
   *
   * ------------------------------------------------------------------------
   * TEMPORARY WORKAROUND — REVERT once akgentic-infra-enterprise Story 10.10 lands.
   * ------------------------------------------------------------------------
   * The Department/Community tiers return a `200` JSON body
   * (`{"success": true, "user": {...}}`) here. The Enterprise tier still returns
   * a `302` redirect (byte-identical to the Department bug fixed in Department
   * Story 2.13). A credentialed `fetch` transparently follows that 302
   * cross-origin into the SPA's HTML, so `await r.json()` on the login response
   * throws and login *appears* to fail even though the session cookie was set.
   *
   * Until Enterprise returns `200` JSON (akgentic-infra-enterprise Story 10.10),
   * do NOT read the login response body: `redirect: 'manual'` avoids following the
   * 302 into HTML, then confirm the session via `GET /auth/me` (the source of
   * truth — same call `checkAuth()` uses). This works against BOTH contracts.
   *
   * REVERT when Story 10.10 ships: restore the single-call body-parse path —
   *     const r = await fetch(url, { credentials: 'include' });
   *     if (!r.ok) throw new Error('Invalid API key');
   *     const user = (await r.json())?.user ?? ANONYMOUS_USER;
   * If `redirect: 'manual'` ever fails to persist the cookie on some browser,
   * drop it — the default `redirect: 'follow'` is proven to set the cookie.
   */
  loginWithApiKey(apiKey: string): Observable<any> {
    const url = `${this.config.api}/auth/login/apikey?apikey=${encodeURIComponent(apiKey)}`;
    return from(
      // The login call binds the session cookie under either contract; we ignore
      // its body and let /auth/me decide whether the key was valid.
      fetch(url, { credentials: 'include', redirect: 'manual' })
        .then(() => fetch(`${this.config.api}/auth/me`, { credentials: 'include' }))
        .then(async (r) => {
          // A 401 from /auth/me (no session bound) means the key was rejected.
          if (!r.ok) {
            throw new Error('Invalid API key');
          }
          const user = await r.json();
          if (user && !user.name) {
            user.name = user.email || user.user_id || 'User';
          }
          this.currentUserSubject.next(user);
          return user;
        })
    );
  }

  /** Logout: redirect to backend logout which clears session and redirects to IdP logout. */
  logout(): void {
    if (this.config.hideLogin) {
      return;
    }
    // Full browser redirect — the backend clears the session and redirects
    // to the IdP's end_session_endpoint, which clears the SSO session and
    // redirects back to /login.
    window.location.href = `${this.config.api}/auth/logout`;
  }
}
