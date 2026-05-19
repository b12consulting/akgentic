import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { ConfigService } from '../services/config.service';
import { LoginComponent } from './login.component';

/**
 * Specs for {@link LoginComponent.loginWithApiKey} (Stories 1.8, 1.9).
 *
 * `AuthService` and `Router` are replaced with `jasmine.SpyObj` stubs — no
 * real navigation or backend call occurs. `AuthService.loginWithApiKey` now
 * emits the authenticated user object on success; the component ignores the
 * value and navigates to `/`.
 */
describe('LoginComponent', () => {
  let component: LoginComponent;
  let authSpy: jasmine.SpyObj<AuthService>;
  let routerSpy: jasmine.SpyObj<Router>;

  function setup(): void {
    authSpy = jasmine.createSpyObj('AuthService', ['loginWithApiKey']);
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);

    TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        { provide: AuthService, useValue: authSpy },
        { provide: Router, useValue: routerSpy },
        {
          provide: ConfigService,
          useValue: {
            api: 'http://backend.test',
            logo: '',
            welcomeMessage: '',
            loginProviders: ['apikey'],
          },
        },
      ],
    });

    component = TestBed.createComponent(LoginComponent).componentInstance;
  }

  beforeEach(() => setup());

  describe('loginWithApiKey — success (AC #1)', () => {
    it('calls AuthService.loginWithApiKey and navigates to / on success', () => {
      authSpy.loginWithApiKey.and.returnValue(of({ user_id: 'u1' }));
      component.apiKey = 'valid-key';

      component.loginWithApiKey();

      expect(authSpy.loginWithApiKey).toHaveBeenCalledWith('valid-key');
      expect(routerSpy.navigate).toHaveBeenCalledWith(['/']);
      expect(component.loading).toBe(false);
      expect(component.error).toBeNull();
    });
  });

  describe('loginWithApiKey — empty key', () => {
    it('sets the error and does not call the service', () => {
      component.apiKey = '';

      component.loginWithApiKey();

      expect(component.error).toBe('Please enter an API key');
      expect(authSpy.loginWithApiKey).not.toHaveBeenCalled();
    });
  });

  describe('loginWithApiKey — error (AC #3)', () => {
    it('surfaces the backend message, resets loading, and does not navigate', () => {
      authSpy.loginWithApiKey.and.returnValue(
        throwError(() => new Error('Invalid API key')),
      );
      component.apiKey = 'bad-key';

      component.loginWithApiKey();

      expect(component.error).toBe('Invalid API key');
      expect(component.loading).toBe(false);
      expect(routerSpy.navigate).not.toHaveBeenCalled();
    });
  });
});
