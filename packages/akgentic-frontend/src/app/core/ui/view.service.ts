import { inject, Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { ConfigService } from '../config/config.service';

@Injectable({
  providedIn: 'root',
})
export class ViewService {
  private config = inject(ConfigService);
  isRightColumnCollapsed$ = new BehaviorSubject<boolean>(false);

  constructor() {
    this.isRightColumnCollapsed$.next(this.config.initRightPanelCollapsed);
  }

  toggleRightColumn(): void {
    this.isRightColumnCollapsed$.next(!this.isRightColumnCollapsed$.value);
  }
}
