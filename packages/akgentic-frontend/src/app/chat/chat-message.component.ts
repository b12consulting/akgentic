import { CommonModule, DatePipe } from '@angular/common';
import { Component, computed, EventEmitter, input, Output } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { MarkdownModule } from 'ngx-markdown';
import { buildPreview, ChatMessage } from '../models/chat-message.model';

@Component({
  selector: 'app-chat-message',
  standalone: true,
  imports: [CommonModule, MarkdownModule, DatePipe, ButtonModule],
  templateUrl: './chat-message.component.html',
  styleUrl: './chat-message.component.scss',
})
export class ChatMessageComponent {
  @Output() messageSelected = new EventEmitter<ChatMessage>();
  @Output() toggleCollapse = new EventEmitter<ChatMessage>();
  @Output() bubbleClicked = new EventEmitter<ChatMessage>();
  @Output() rule3Clicked = new EventEmitter<ChatMessage>();
  message = input.required<ChatMessage>();
  selected = input<boolean>(false);
  notification = input<boolean>(false);

  readonly preview = computed(() => buildPreview(this.message().content));

  onToggleCollapse(): void {
    const msg = this.message();
    if (msg.rule === 3 || msg.rule === 4) {
      this.toggleCollapse.emit(msg);
    }
  }

  onLabelClick(): void {
    const msg = this.message();
    if (msg.rule !== 1) {
      this.messageSelected.emit(msg);
    }
  }

  onBubbleClick(event: Event): void {
    event.stopPropagation();
    const msg = this.message();
    switch (msg.rule) {
      case 1:
      case 2:
        this.bubbleClicked.emit(msg);
        break;
      case 3:
      case 4:
        this.onToggleCollapse();
        break;
    }
  }

  onOpenModal(event: Event): void {
    event.stopPropagation();
    this.rule3Clicked.emit(this.message());
  }
}
