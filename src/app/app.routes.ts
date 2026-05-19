import { Routes } from '@angular/router';
import { AuthGuard } from './auth.guard';
import { HomeComponent } from './home/home.component';
import { LoginComponent } from './login/login.component';
import { ProcessComponent } from './process/process.component';
import { namespacePanelCanDeactivate } from './admin/catalog/namespace-panel/namespace-panel.guard';

export const routes: Routes = [
  {
    path: '',
    component: HomeComponent,
    title: 'Home page',
    canActivate: [AuthGuard],
  },
  {
    path: 'process/:id',
    component: ProcessComponent,
    title: 'Process page',
    canActivate: [AuthGuard],
  },
  {
    // Story 11.6 — deep-link route for the catalog namespace panel.
    // `loadComponent` keeps the panel (and its Monaco bundle) out of the
    // initial home-page chunk (NFR8). The functional `CanDeactivate` guard
    // prompts before losing an operator's unsaved edit buffer.
    path: 'admin/catalog/namespace/:namespace',
    loadComponent: () =>
      import(
        './admin/catalog/namespace-panel/namespace-panel-route.component'
      ).then((m) => m.NamespacePanelRouteComponent),
    title: 'Catalog namespace',
    canActivate: [AuthGuard],
    canDeactivate: [namespacePanelCanDeactivate],
  },
  { path: 'login', component: LoginComponent, title: 'Login page' },
];
