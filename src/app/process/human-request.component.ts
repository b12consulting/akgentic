import { CommonModule } from '@angular/common';
import { Component, inject, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { TextareaModule } from 'primeng/textarea';
import { BehaviorSubject } from 'rxjs';

import { GraphDataService } from '../services/graph-data.service';
import { SelectionService } from '../services/selection.service';

import { SentMessage } from '../models/message.types';
import { LineBreakPipe } from '../pipes/line_break.pipe';

@Component({
  selector: 'app-human-request',
  standalone: true,
  imports: [
    DialogModule,
    FormsModule,
    CommonModule,
    TextareaModule,
    ButtonModule,
    LineBreakPipe,
  ],
  providers: [],
  template: `
    <p-dialog
      *ngIf="userRequest$ | async as userRequest"
      [header]="userRequest?.recipient?.name || 'User Input Required'"
      [modal]="true"
      [visible]="(modalVisible$ | async) ?? false"
      (visibleChange)="onHide()"
      [style]="{ width: '50rem' }"
      [contentStyle]="{ overflow: 'visible' }"
    >
      <div class="flex items-center gap-4 mb-8">
        <div for="username" style="margin-bottom: 0.3rem"></div>
      </div>
      <div class="mb-4">
        <div
          class="mb-8 font-bold"
          [innerHTML]="userRequest?.message?.content | lineBreak"
        ></div>
        <textarea
          pTextarea
          style="width: 100%; min-height: 120px; max-height: 200px"
          [autoResize]="true"
          [ngModel]="userInput$ | async"
          (ngModelChange)="onInputChange($event)"
          (keydown.meta.enter)="onSave()"
          (keydown.control.enter)="onSave()"
        ></textarea>
      </div>
      <div class="flex justify-end gap-2">
        <p-button label="Save" (click)="onSave()" />
      </div>
    </p-dialog>
  `,
})
export class HumanRequestComponent {
  @Input() nodes: any[] = [];
  selectionService: SelectionService = inject(SelectionService);
  graphDataService: GraphDataService = inject(GraphDataService);

  userRequest$ = this.selectionService.userRequest$;
  modalVisible$ = this.selectionService.modalVisible$;
  userInput$ = new BehaviorSubject<string>('');

  onHide() {
    this.modalVisible$.next(false);
  }

  onInputChange(event: any) {
    this.userInput$.next(event);
  }

  onSave() {
    const requestMessage = this.userRequest$.value as SentMessage;
    this.selectionService.onSave(this.userInput$.value, requestMessage);
    this.userInput$.next('');
  }
}
