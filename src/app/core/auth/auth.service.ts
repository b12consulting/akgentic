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
   * Drives `POST /auth/login/apikey` with a JSON body (`{"apikey": "<key>"}`)
   * and `credentials: 'include'` so the backend's `Set-Cookie` session response
   * is stored by the browser. One code path, no tier-specific branching: the
   * endpoint is owned by the shared backend auth library, so every tier that
   * adopts it serves the same contract. (Enterprise has not adopted it yet and
   * still serves a legacy variant; that is tracked backend-side, and this client
   * deliberately does not branch to accommodate it.)
   *
   * The API key is sent once in the request body — never in the URL, where it
   * would be captured by access logs, browser history, and `Referer` headers —
   * and is NEVER persisted in the browser; the session cookie set by the backend
   * is the sole post-login credential, exactly as on the OAuth path.
   *
   * On an HTTP `2xx` response the backend returns a `200` JSON body
   * (`{"success": true, "user": {...}}`) — no redirect. The JSON body itself
   * is the success signal: its `user` is used to refresh `currentUserSubject`
   * and the observable completes with that user. On a non-OK response (e.g.
   * HTTP 401 for an invalid, unknown, or expired key) the observable errors so
   * the caller can surface the message.
   */
  loginWithApiKey(apiKey: string): Observable<any> {
    const url = `${this.config.api}/auth/login/apikey`;
    const options: RequestInit = {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apikey: apiKey }),
    };
    return from(
      fetch(url, options).then(async (r) => {
        // A 401 (invalid/unknown/expired key) is not ok — error the observable.
        if (!r.ok) {
          throw new Error('Invalid API key');
        }
        // Success: the backend binds the session and returns a 200 JSON body.
        const body = await r.json();
        const user = body?.user ?? ANONYMOUS_USER;
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
