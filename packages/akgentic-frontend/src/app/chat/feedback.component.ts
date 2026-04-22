import { CommonModule } from '@angular/common';
import { Component, inject, input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Button } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { Textarea } from 'primeng/textarea';
import { BehaviorSubject } from 'rxjs';
import { ChatMessage } from '../models/chat-message.model';
import { Feedback, FeedbackService } from '../services/feedback.service';

@Component({
  selector: 'app-feedback',
  standalone: true,
  imports: [DialogModule, FormsModule, CommonModule, Textarea, Button],
  styleUrls: ['./feedback.component.scss'],
  template: `
    <div class="feedback-icons" *ngIf="message().rule !== 1">
      <i
        class="pi pi-thumbs-up"
        [class.selected]="
          (hasFeedback$ | async) && (isPositiveFeedback$ | async)
        "
        (click)="openFeedbackModal(true)"
      ></i>
      <i
        class="pi pi-thumbs-down"
        [class.selected]="
          (hasFeedback$ | async) && !(isPositiveFeedback$ | async)
        "
        (click)="openFeedbackModal(false)"
      ></i>
    </div>

    <p-dialog
      [visible]="(displayModal$ | async) ?? false"
      [modal]="true"
      [style]="{ width: '50vw' }"
      [draggable]="false"
      [resizable]="false"
      (visibleChange)="onCancel()"
    >
      <ng-template pTemplate="header">
        <div class="modal-header">
          <span>Feedback</span>
          <div class="modal-selected-thumb">
            <i
              *ngIf="isPositiveFeedback$ | async"
              class="pi pi-thumbs-up selected"
            ></i>
            <i
              *ngIf="!(isPositiveFeedback$ | async)"
              class="pi pi-thumbs-down selected"
            ></i>
          </div>
        </div>
      </ng-template>

      <textarea
        pTextarea
        rows="10"
        style="width: 100%"
        [ngModel]="feedbackComment$ | async"
        (ngModelChange)="onInputChange($event)"
        placeholder="Enter your feedback here..."
      ></textarea>
      <ng-template pTemplate="footer">
        <p-button
          pRipple
          type="button"
          icon="pi pi-times"
          (click)="onCancel()"
          label="Cancel"
          class="p-button-text"
        ></p-button>
        <p-button
          pRipple
          type="button"
          icon="pi pi-check"
          (click)="submitFeedback()"
          label="Submit"
        ></p-button>
      </ng-template>
    </p-dialog>
  `,
})
export class FeedbackComponent {
  feedbackService = inject(FeedbackService);
  message = input.required<ChatMessage>();

  displayModal$ = new BehaviorSubject<boolean>(false);
  feedbackComment$ = new BehaviorSubject<string>('');
  isPositiveFeedback$ = new BehaviorSubject<boolean>(false);
  hasFeedback$ = new BehaviorSubject<boolean>(false);
  selectedThumb$ = new BehaviorSubject<'up' | 'down' | null>(null);

  async ngOnInit() {
    await this.feedbackService.loadFeedback();
    this.feedbackService.feedbacks$.subscribe((feedbacks) => {
      const feedback = feedbacks.find(
        (f) => f.message.id === this.message().id
      );
      if (feedback) {
        this.hasFeedback$.next(true);
        this.isPositiveFeedback$.next(feedback.isPositive);
        this.feedbackComment$.next(feedback.comment);
      }
    });
  }

  onInputChange(event: any) {
    this.feedbackComment$.next(event);
  }

  openFeedbackModal(isPositive: boolean) {
    this.displayModal$.next(true);
    this.isPositiveFeedback$.next(isPositive);
    this.selectedThumb$.next(isPositive ? 'up' : 'down');
  }

  submitFeedback() {
    this.hasFeedback$.next(true);
    const feedback: Feedback = {
      message: this.message(),
      isPositive: this.isPositiveFeedback$.value,
      comment: this.feedbackComment$.value,
    };
    const msgId = this.message().id;
    if (!msgId) {
      console.error('No id found in message');
      this.displayModal$.next(false);
      return;
    }
    this.feedbackService.setFeedback(msgId, feedback);
    this.displayModal$.next(false);
  }

  onCancel() {
    this.displayModal$.next(false);
  }
}
