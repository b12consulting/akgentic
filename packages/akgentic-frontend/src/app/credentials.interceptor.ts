import {
  HttpEvent,
  HttpHandler,
  HttpInterceptor,
  HttpRequest,
} from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ConfigService } from './services/config.service';

@Injectable()
export class CredentialsInterceptor implements HttpInterceptor {
  private config = inject(ConfigService);

  intercept(
    req: HttpRequest<any>,
    next: HttpHandler
  ): Observable<HttpEvent<any>> {
    // Community tier (hideLogin=true): pass through without credentials
    if (this.config.hideLogin) {
      return next.handle(req);
    }
    const clonedReq = req.clone({ withCredentials: true });
    return next.handle(clonedReq);
  }
}
