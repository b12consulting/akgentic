import { Component, DestroyRef, inject } from '@angular/core';
import { Router, RouterModule, RouterOutlet } from '@angular/router';

import { CommonModule } from '@angular/common';
import { MenuItem } from 'primeng/api';
import { MenubarModule } from 'primeng/menubar';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';
import { Subject, takeUntil } from 'rxjs';
import { emptyableCombineLatest } from './lib/util';
import { AkgentService } from './services/akgent.service';
import { ApiService } from './services/api.service';
import { AuthService } from './services/auth.service';
import { ConfigService } from './services/config.service';
import { ContextService } from './services/context.service';
import { FaviconService } from './services/favicon.service';
import { ViewService } from './view.service';

@Component({
  selector: 'app-root',
  imports: [
    RouterOutlet,
    RouterModule,
    MenubarModule,
    ToastModule,
    TagModule,
    CommonModule,
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {
  title = 'akgent-app';
  items: MenuItem[] | undefined;
  private configService = inject(ConfigService);
  logo: string = '';
  hideLogin: boolean = true;

  akgentService: AkgentService = inject(AkgentService);
  authService: AuthService = inject(AuthService);
  contextService: ContextService = inject(ContextService);
  viewService: ViewService = inject(ViewService);
  destroyRef = inject(DestroyRef);
  faviconService = inject(FaviconService);
  apiService = inject(ApiService);
  router = inject(Router);

  ngOnInit() {
    this.logo = this.configService.logo;
    this.hideLogin = this.configService.hideLogin;
    this.faviconService.setFavicon(this.configService.favicon);

    // Fetch the authenticated user from the backend session
    this.authService.checkAuth().subscribe();
    const destroyed = new Subject();

    this.destroyRef.onDestroy(() => {
      destroyed.next(null);
      destroyed.complete();
    });

    emptyableCombineLatest([
      this.contextService.currentProcessId$.asObservable(),
      this.authService.currentUser$,
      this.viewService.isRightColumnCollapsed$,
    ])
      .pipe(takeUntil(destroyed))
      .subscribe(([processId, currentUser, isRightColumnCollapsed]) => {
        this.items = [
          {
            icon: 'pi pi-home',
            label: 'Home',
            route: ['/'],
            command: () => {
              this.contextService.currentProcessId$.next('');
            },
          },
          {
            icon: 'pi pi-eraser',
            label: 'Clear',
            command: () => {
              this.clear();
            },
            disabled: processId === '',
          },
          {
            icon: isRightColumnCollapsed
              ? 'pi pi-arrow-left'
              : 'pi pi-arrow-right',
            label: isRightColumnCollapsed ? 'Show details' : 'Hide details',
            command: () => {
              this.viewService.toggleRightColumn();
            },
            visible: processId !== '',
          },
          // Username dropdown menu at end (only when authenticated)
          ...(currentUser && currentUser.name
            ? [
                {
                  label: currentUser.name,
                  icon: 'pi pi-user',
                  styleClass: 'username-menu',
                  items: [
                    {
                      label: 'Logout',
                      icon: 'pi pi-power-off',
                      command: () => {
                        this.authService.logout();
                      },
                    },
                  ],
                },
              ]
            : []),
        ].filter((item) =>
          this.configService.hideHome ? item.label != 'Home' : true,
        );
      });
  }

  // Clear the current process and create a new one of the same type
  async clear() {
    const processId = this.contextService.currentProcessId$.value;
    this.contextService.clear(processId);
  }

  // Navigate to home page and clear current process context
  navigateToHome() {
    this.contextService.currentProcessId$.next('');
    this.router.navigate(['/']);
  }
}
