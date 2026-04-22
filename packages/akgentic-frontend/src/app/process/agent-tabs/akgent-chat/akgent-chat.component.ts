import { Component, inject, Input, ViewChild, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { FieldsetModule } from 'primeng/fieldset';
import { FloatLabelModule } from 'primeng/floatlabel';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { Table, TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';

import { BehaviorSubject } from 'rxjs';

import { CapitalizePipe } from '../../../pipes/capitalise.pipe';
import { ApiService } from '../../../services/api.service';
import { UtilService } from '../../../services/utils.service';
import { ContextService } from '../../../services/context.service';
import { ActorMessageService } from '../../../services/message.service';

import { CopyButtonComponent } from '../../copy-button/copy-button.component';

/**
 * AkgentChatComponent - Displays chat messages with JSON-formatted arguments using Monaco Editor
 */

@Component({
  selector: 'app-akgent-chat',
  imports: [
    CommonModule,
    TableModule,
    CardModule,
    FieldsetModule,
    FormsModule,
    TextareaModule,
    FloatLabelModule,
    ButtonModule,
    ProgressSpinnerModule,
    CapitalizePipe,
    CopyButtonComponent,
  ],
  templateUrl: './akgent-chat.component.html',
  styleUrl: './akgent-chat.component.scss',
})
export class AkgentChatComponent {
  @ViewChild('dataTable') dataTable!: Table;
  @Input() context$!: BehaviorSubject<any[]>;
  @Input() agentId!: string;
  @Input() agentName!: string;

  apiService: ApiService = inject(ApiService);
  utilService: UtilService = inject(UtilService);
  contextService: ContextService = inject(ContextService);
  messageService: ActorMessageService = inject(ActorMessageService);
  private destroyRef = inject(DestroyRef);

  collapsedMessages$ = new BehaviorSubject<boolean>(true);

  context: any[] = [];

  ngOnInit(): void {
    // Subscribe to context$ for the selected agent
    this.context$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((ctx) => {
      if (this.isLoading) {
        setTimeout(() => this.scroll(), 10);
      }
      this.isLoading = false;
      this.updateContext(ctx);
    });
  }

  updateContext(context: any[]) {
    if (!context || context.length === 0) {
      this.context = [];
      return;
    }

    // First pass: collect all messages
    const allMessages = context.flatMap((message: any) => {
      const msg: any[] = [];

      // Handle new parts-based protocol
      if (message.parts && Array.isArray(message.parts)) {
        message.parts.forEach((part: any) => {
          if (
            part.part_kind === 'system-prompt' ||
            part.part_kind === 'user-prompt'
          ) {
            msg.push({
              type: part.part_kind === 'system-prompt' ? 'system' : 'human',
              name:
                part.part_kind === 'system-prompt'
                  ? part.dynamic_ref
                    ? part.dynamic_ref.split('.').pop()
                    : 'System'
                  : 'User',
              content: part.content,
              timestamp: part.timestamp,
            });
          } else if (part.part_kind === 'tool-call') {
            // Parse args if it's a string
            let args = part.args;
            if (typeof args === 'string') {
              try {
                args = JSON.parse(args);
              } catch (e) {
                // Keep as string if parsing fails
              }
            }

            msg.push({
              type: 'tool_call',
              name: part.tool_name,
              args: args,
              tool_call_id: part.tool_call_id,
              timestamp: part.timestamp,
              result: null, // Will be filled later
            });
          } else if (
            part.part_kind === 'tool-return' ||
            part.part_kind === 'retry-prompt'
          ) {
            msg.push({
              type: 'tool_return_data',
              content: part.content,
              tool_call_id: part.tool_call_id,
              timestamp: part.timestamp,
            });
          } else if (part.part_kind === 'text' && part.content) {
            // Handle AI text responses (including structured outputs)
            msg.push({
              type: 'ai',
              name: 'Assistant',
              content: part.content,
              timestamp: part.timestamp || message.timestamp,
              usage: message.usage,
              model_name: message.model_name,
              provider_name: message.provider_name,
            });
          }
        });
      }
      // Handle response-type messages (AI responses)
      else if (
        message.kind === 'response' &&
        message.parts &&
        Array.isArray(message.parts)
      ) {
        message.parts.forEach((part: any) => {
          if (part.part_kind === 'tool-call') {
            // Parse args if it's a string
            let args = part.args;
            if (typeof args === 'string') {
              try {
                args = JSON.parse(args);
              } catch (e) {
                // Keep as string if parsing fails
              }
            }

            msg.push({
              type: 'tool_call',
              name: part.tool_name,
              args: args,
              tool_call_id: part.tool_call_id,
              timestamp: part.timestamp || message.timestamp,
              result: null, // Will be filled later
            });
          } else if (part.content) {
            // Regular AI response content
            msg.push({
              type: 'ai',
              name: 'Assistant',
              content: part.content,
              timestamp: part.timestamp || message.timestamp,
              usage: message.usage,
              model_name: message.model_name,
              provider_name: message.provider_name,
            });
          }
        });
      } else {
        // Handle legacy format for backward compatibility
        if (
          ['system', 'ai', 'human'].includes(message.type) &&
          message.content
        ) {
          msg.push({
            type: message.type,
            name: message.name,
            content: message.content,
          });
        }
        if (message.tool_calls?.length) {
          msg.push(
            ...message.tool_calls.map((tool_call: any) => ({
              type: 'tool_call',
              name: tool_call.name,
              args: tool_call.args,
              result:
                context.find((m: any) => m.tool_call_id === tool_call.id)
                  ?.content || null,
            }))
          );
        }
      }
      return msg;
    });

    // Second pass: match tool calls with their results
    allMessages.forEach((message: any) => {
      if (message.type === 'tool_call' && message.result === null) {
        const toolReturn = allMessages.find(
          (m: any) =>
            m.type === 'tool_return_data' &&
            m.tool_call_id === message.tool_call_id
        );
        if (toolReturn) {
          message.result = toolReturn.content;
        }
      }
    });

    // Filter out tool_return_data as they're now merged with tool_call messages
    this.context = allMessages.filter(
      (message: any) => message.type !== 'tool_return_data'
    );

    setTimeout(() => this.scroll(), 0);
    this.initialLoad = false;
  }

  userInput = '';
  isLoading = false;
  async sendMessage() {
    if (!this.contextService.currentTeamRunning$.value) return;
    this.isLoading = true;
    const processId = this.contextService.currentProcessId$.value;
    try {
      await this.apiService.sendMessage(
        processId,
        this.userInput,
        this.agentName
      );
    } catch (error) {
      this.isLoading = false;
    }
    this.userInput = '';
  }

  initialLoad = true;
  isMouseOverTable: boolean = false; // Track mouse hover state
  scroll(behavior: string = 'smooth') {
    if (!this.isMouseOverTable && this.dataTable && !this.initialLoad) {
      const body =
        this.dataTable.containerViewChild?.nativeElement.getElementsByClassName(
          'p-datatable-table-container'
        )[0];
      body.scrollTo({
        top: body.scrollHeight,
        behavior: behavior,
      });
    }
  }

  toggleCollapse() {
    this.collapsedMessages$.next(!this.collapsedMessages$.value);
  }
}
