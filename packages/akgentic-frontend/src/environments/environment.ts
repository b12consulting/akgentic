import { AuthProvider } from '../app/core/auth/auth.types';

export const environment = {
  production: true,
  api: 'http://localhost:8000',
  catalogVersion: 'v1' as const,
  logo: 'b12.png',
  autoRedirectContext: 'Business team',
  welcomeMessage: 'Welcome to the Akgentic Framework',
  hideHome: false,
  hideLogin: true,
  initRightPanelCollapsed: false,
  userInputEnterKeySubmit: false,
  favicon: 'favicon.png',
  loginProviders: ['apikey'] as AuthProvider[] // Array, ordered by preference
};
