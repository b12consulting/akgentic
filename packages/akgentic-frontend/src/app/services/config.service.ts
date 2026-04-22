import { Injectable } from '@angular/core';
import { Environment, AuthProvider } from '../models/auth.types';
import { environment } from '../../environments/environment';

/**
 * Runtime configuration service.
 *
 * Fetches `/config.json` at app startup (via APP_INITIALIZER) and merges it
 * over the build-time `environment.ts` defaults. In local dev (no config.json
 * served), the build-time defaults are used as-is.
 *
 * All components and services should inject this service instead of importing
 * `environment` directly.
 */
@Injectable({ providedIn: 'root' })
export class ConfigService {
  private config: Environment = { ...environment };

  /** Called once by APP_INITIALIZER before the app renders. */
  async load(): Promise<void> {
    try {
      const response = await fetch('/config.json');
      if (response.ok) {
        const runtime = await response.json();
        this.config = { ...this.config, ...runtime };
      }
    } catch {
      // config.json not available (local dev) — use build-time defaults
    }
  }

  get api(): string {
    return this.config.api;
  }

  get logo(): string {
    return this.config.logo;
  }

  get welcomeMessage(): string {
    return this.config.welcomeMessage;
  }

  get loginProviders(): AuthProvider[] {
    return this.config.loginProviders;
  }

  get autoRedirectContext(): string {
    return this.config.autoRedirectContext;
  }

  get hideHome(): boolean {
    return this.config.hideHome;
  }

  get hideLogin(): boolean {
    return this.config.hideLogin;
  }

  get initRightPanelCollapsed(): boolean {
    return this.config.initRightPanelCollapsed;
  }

  get userInputEnterKeySubmit(): boolean {
    return this.config.userInputEnterKeySubmit;
  }

  get favicon(): string {
    return this.config.favicon;
  }

  get production(): boolean {
    return this.config.production;
  }
}
