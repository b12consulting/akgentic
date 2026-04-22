import { inject, Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { ConfigService } from './config.service';
import { ChatMessage } from '../models/chat-message.model';
import { chatFold } from './chat.service';
import { FetchService } from './fetch.service';
import { MessageLogService } from './message-log.service';

export interface Feedback {
  message: ChatMessage;
  isPositive: boolean;
  comment: string;
  feedback_id?: string;
}

export interface FeedbackBackend {
  comment: string;
  score?: number;
}

@Injectable()
export class FeedbackService {
  fetchService: FetchService = inject(FetchService);
  private readonly log: MessageLogService = inject(MessageLogService);
  private config = inject(ConfigService);

  /** Snapshot of the current derived chat message list via `chatFold` over
   *  the unified message log. Replaces the pre-refactor
   *  `chatService.messages$.value` access — the selector is now read-only
   *  (Story 6.3). */
  private currentMessages(): ChatMessage[] {
    return chatFold(this.log.snapshot()).messages;
  }

  feedbacks$: BehaviorSubject<Feedback[]> = new BehaviorSubject<Feedback[]>([]);

  async getFeedback(run_id: string): Promise<any> {
    const response = await this.fetchService.fetch({
      url: `${this.config.api}/get-feedback?run_id=${run_id}`,
    });
    return response;
  }

  async setFeedback(run_id: string, feedback: Feedback) {
    const feedback_id = await this.fetchService.fetch({
      url: `${this.config.api}/set-feedback`,
      options: {
        method: 'POST',
        body: JSON.stringify({
          feedback: this.frontendFeedbackToBackendFeedback(feedback),
          run_id,
        }),
        headers: { 'Content-Type': 'application/json' },
      },
    });

    feedback.feedback_id = feedback_id;

    this.feedbacks$.next([...this.feedbacks$.value, feedback]);
  }

  backendFeedbackToFrontendFeedback(
    run_id: string,
    feedback: FeedbackBackend
  ): Feedback | null {
    const message = this.currentMessages().find(
      (m) => m.id === run_id
    );
    if (!message) return null;

    return {
      message: message,
      isPositive: feedback.score === 1,
      comment: feedback.comment,
    };
  }

  frontendFeedbackToBackendFeedback(feedback: Feedback): FeedbackBackend {
    return {
      comment: feedback.comment,
      score: feedback.isPositive ? 1 : 0,
    };
  }

  async loadFeedback() {
    // Feedback loading is deferred — derived chat state may be empty until
    // feedback integration is wired in a future story.
    const messages = this.currentMessages();
    if (messages.length === 0) return;

    const feedbacks = await Promise.all(
      messages.map(async (message) => {
        const feedback: FeedbackBackend = await this.getFeedback(message.id);
        if (!feedback?.score) return null;
        return this.backendFeedbackToFrontendFeedback(message.id, feedback);
      })
    );

    const filteredFeedbacks = feedbacks.filter((feedback) => feedback !== null);

    this.feedbacks$.next(filteredFeedbacks);
  }
}
