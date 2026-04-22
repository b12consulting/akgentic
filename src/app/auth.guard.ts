import { inject, Injectable } from '@angular/core';
import { CanActivate, Router } from '@angular/router';
import { Observable, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AuthService } from './services/auth.service';
import { ConfigService } from './services/config.service';

@Injectable({ providedIn: 'root' })
export class AuthGuard implements CanActivate {
  authService: AuthService = inject(AuthService);
  router: Router = inject(Router);
  config: ConfigService = inject(ConfigService);

  canActivate(): Observable<boolean> {
    // Community tier: skip auth entirely
    if (this.config.hideLogin) {
      return of(true);
    }

    return this.authService.checkAuth().pipe(
      map((user) => {
        if (user) {
          return true;
        } else {
          this.router.navigate(['/login']);
          return false;
        }
      }),
      catchError((err) => {
        this.router.navigate(['/login']);
        return of(false);
      })
    );
  }
}
