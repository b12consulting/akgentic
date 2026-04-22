import { AuthProvider } from '../app/models/auth.types';

export const environment = {
  production: true,
  api: 'http://localhost:8000',
  logo: 'b12.png',
  autoRedirectContext: 'Business team',
  welcomeMessage: 'Welcome to the B12 Akgentic Framework',
  hideHome: false,
  hideLogin: true,
  initRightPanelCollapsed: false,
  userInputEnterKeySubmit: false,
  favicon: 'favicon.png',
  loginProviders: ['google', 'apikey'] as AuthProvider[] // Array, ordered by preference
};
