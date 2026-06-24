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
  /**
   * Catalog API generation the backend exposes. `v2` (default) is the
   * namespaced catalog (department tier); `v1` is the flat, namespace-less
   * catalog (enterprise tier) where teams are created from a catalog entry id.
   */
  catalogVersion?: 'v1' | 'v2';
}
