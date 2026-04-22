import { CommonModule, DatePipe } from '@angular/common';
import { Component, EventEmitter, input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { TextareaModule } from 'primeng/textarea';
import { MarkdownModule } from 'ngx-markdown';

import { ChatMessage } from '../models/chat-message.model';
import { ActorAddress } from '../models/message.types';
import { makeAgentNameUserFriendly } from '../lib/util';

export interface HumanModalReply {
  content: string;
  messageId: string;
}

/**
 * A request/reply pair for already-answered Rule 3 messages displayed in
 * the modal's greyed-out "Answered" section. See Story 4-7 AC1/AC4.
 */
export interface AnsweredRequest {
  request: ChatMessage;
  reply: ChatMessage;
}

@Component({
  selector: 'app-chat-human-modal',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    DialogModule,
    TextareaModule,
    ButtonModule,
    MarkdownModule,
    DatePipe,
  ],
  templateUrl: './chat-human-modal.component.html',
  styleUrl: './chat-human-modal.component.scss',
})
export class ChatHumanModalComponent {
  visible = input<boolean>(false);
  agentPair = input<{ sender: ActorAddress; recipient: ActorAddress } | null>(null);
  pendingMessages = input<ChatMessage[]>([]);
  answeredMessages = input<AnsweredRequest[]>([]);

  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() reply = new EventEmitter<HumanModalReply>();

  /** Per-request reply buffer, keyed on `request.id`. */
  replyBuffers: Map<string, string> = new Map();

  get headerText(): string {
    const pair = this.agentPair();
    if (!pair) return 'Human Input';
    const sender = makeAgentNameUserFriendly(pair.sender.name);
    const recipient = makeAgentNameUserFriendly(pair.recipient.name);
    return `${sender} ⇒ ${recipient}`;
  }

  getReplyBuffer(id: string): string {
    return this.replyBuffers.get(id) ?? '';
  }

  setReplyBuffer(id: string, value: string): void {
    this.replyBuffers.set(id, value);
  }

  onVisibleChange(value: boolean): void {
    if (!value) {
      this.replyBuffers.clear();
      this.visibleChange.emit(false);
    }
  }

  onSendForRequest(requestId: string): void {
    const buffer = (this.replyBuffers.get(requestId) ?? '').trim();
    if (!buffer) return;
    this.reply.emit({ content: buffer, messageId: requestId });
    this.replyBuffers.delete(requestId);
    // Modal stays open — parent reclassifies pending/answered lists.
  }

  trackByRequestId(_index: number, msg: ChatMessage): string {
    return msg.id;
  }

  trackByAnsweredId(_index: number, a: AnsweredRequest): string {
    return a.request.id;
  }
}
