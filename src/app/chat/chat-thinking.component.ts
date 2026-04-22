import { CommonModule } from '@angular/common';
import { Component, input, output } from '@angular/core';

import { ThinkingState, ThinkingToolEntry } from '../services/chat.service';

/**
 * Story 4-8: Per-agent thinking bubble with tool-call history.
 *
 * Renders two visual modes, gated by `state.final`:
 *   - Ephemeral (final=false): header + blinking dots; tool list only if
 *     `expanded()`.
 *   - Persistent (final=true): header (no dots) + full tool list,
 *     unconditionally.
 *
 * Clicking the bubble emits `toggleExpanded` with the state's
 * `anchor_message_id`; the parent component owns the expansion Set so it
 * survives re-emissions of `thinkingAgents$`.
 */
@Component({
  selector: 'app-chat-thinking',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './chat-thinking.component.html',
  styleUrl: './chat-thinking.component.scss',
})
export class ChatThinkingComponent {
  state = input.required<ThinkingState>();
  expanded = input<boolean>(false);
  toggleExpanded = output<string>();

  onClick(): void {
    this.toggleExpanded.emit(this.state().anchor_message_id);
  }

  /** Stable tracking so a new tool append does not re-render existing rows. */
  trackByToolId(_: number, t: ThinkingToolEntry): string {
    return t.tool_call_id;
  }
}
