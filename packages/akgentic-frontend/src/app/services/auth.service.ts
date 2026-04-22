import { inject, Injectable } from '@angular/core';
import { BehaviorSubject, from, Observable, of } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';
import { ConfigService } from './config.service';

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

  /** @deprecated Use OAuth flow instead. */
  loginWithApiKey(_apiKey: string): Observable<any> {
    return of({ success: true, user: this.currentUserValue });
  }

  /** @deprecated */
  setApiKey(_apiKey: string): void {}

  /** @deprecated */
  clearApiKey(): void {}

  /** @deprecated */
  getApiKey(): string | null {
    return null;
  }

  /** @deprecated */
  getAuthHeaders(): any {
    return {};
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
