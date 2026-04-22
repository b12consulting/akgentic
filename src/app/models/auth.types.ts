export type AuthProvider = 'azure' | 'google' | 'apikey' | 'default' | (string & {});

export interface Environment {
  production: boolean;
  api: string;
  logo: string;
  welcomeMessage: string;
  loginProviders: AuthProvider[];
  providerLabels?: Record<AuthProvider, string>;
  autoRedirectContext: string;
  hideHome: boolean;
  hideLogin: boolean;
  initRightPanelCollapsed: boolean;
  userInputEnterKeySubmit: boolean;
  favicon: string;
}
