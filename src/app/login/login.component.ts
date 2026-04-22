import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../services/auth.service';
import { ConfigService } from '../services/config.service';
import { AuthProvider } from '../models/auth.types';

@Component({
  selector: 'app-login',
  templateUrl: './login.component.html',
  styleUrls: ['./login.component.scss'],
  imports: [CommonModule, FormsModule],
  standalone: true,
})
export class LoginComponent {
  private config = inject(ConfigService);

  // Configuration
  logo: string = this.config.logo;
  welcomeMessage: string = this.config.welcomeMessage;
  apiBaseUrl: string = this.config.api;

  // Multi-provider configuration
  loginProviders: AuthProvider[] = this.config.loginProviders;

  // UI State
  activeProvider: AuthProvider | '';
  showTabs: boolean;

  // Provider-specific state
  apiKey: string = '';
  loading: boolean = false;
  error: string | null = null;

  private authService = inject(AuthService);
  private router = inject(Router);

  constructor() {
    // Initialize with first provider or show error
    if (this.loginProviders.length === 0) {
      this.error = 'No authentication providers configured';
      this.activeProvider = '';
      this.showTabs = false;
    } else {
      this.activeProvider = this.loginProviders[0];
      this.showTabs = this.loginProviders.length > 1;
    }
  }

  // Tab navigation
  selectProvider(provider: AuthProvider): void {
    this.activeProvider = provider;
    this.error = null; // Clear errors when switching tabs
  }

  isProviderActive(provider: AuthProvider): boolean {
    return this.activeProvider === provider;
  }

  isProviderEnabled(provider: AuthProvider): boolean {
    return this.loginProviders.includes(provider);
  }

  getProviderLabel(provider: AuthProvider): string {
    const labels: Record<string, string> = {
      azure: 'Azure AD',
      google: 'Google',
      apikey: 'API Key',
      default: 'Sign In',
    };
    return labels[provider] || provider.charAt(0).toUpperCase() + provider.slice(1);
  }

  /** Returns true if the provider is an OAuth/OIDC provider (not API key). */
  isOAuthProvider(provider: AuthProvider): boolean {
    return provider !== 'apikey';
  }

  /** Redirects to the backend OAuth login endpoint for any provider. */
  loginOAuth(provider: AuthProvider): void {
    window.location.href = `${this.apiBaseUrl}/auth/login/${provider}`;
  }

  /**
   * Authenticates the user using API key
   */
  loginWithApiKey(): void {
    if (!this.apiKey) {
      this.error = 'Please enter an API key';
      return;
    }

    this.loading = true;
    this.error = null;

    this.authService.loginWithApiKey(this.apiKey).subscribe({
      next: (response) => {
        this.loading = false;
        if (response.success) {
          // Store the API key for future requests
          this.authService.setApiKey(this.apiKey);
          // Navigate to home page
          this.router.navigate(['/']);
        } else {
          this.error = response.error || 'Unknown authentication error';
        }
      },
      error: (err) => {
        this.loading = false;
        this.error =
          err.error?.error ||
          'Failed to authenticate with the provided API key';
      },
    });
  }
}
