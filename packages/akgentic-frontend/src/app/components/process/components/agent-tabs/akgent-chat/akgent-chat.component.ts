import {
  Component,
  ElementRef,
  inject,
  Input,
  OnChanges,
  OnInit,
  SimpleChanges,
  ViewChild,
  DestroyRef,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { FieldsetModule } from 'primeng/fieldset';
import { FloatLabelModule } from 'primeng/floatlabel';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { TextareaModule } from 'primeng/textarea';
import { MentionModule } from 'angular-mentions';

import { BehaviorSubject, combineLatest, Observable, of } from 'rxjs';
import { map } from 'rxjs/operators';

import { CapitalizePipe } from '../../../../../shared/pipes/capitalise.pipe';
import { TokenCountPipe } from '../../../../../shared/pipes/token-count.pipe';
import { ApiService } from '../../../../../core/http/api.service';
import { UtilService } from '../../../../../core/ui/utils.service';
import { ContextService } from '../../../../../core/context/context.service';
import { ConfigService } from '../../../../../core/config/config.service';
import { IngestionService } from '../../../event/ingestion.service';
import {
  SystemPromptRow,
  SystemPromptSelector,
  systemPromptLabel,
} from '../../../selectors/system-prompt.selector';
import { TokenUsageSelector } from '../../../selectors/token-usage.selector';
import {
  AgentTokenUsage,
  CONVERSATION_SUMMARY_PREFIX,
} from '../../../event/per-agent-specs';
import { CommandDescriptor } from '../../../../../protocol/message.types';

import { CopyButtonComponent } from '../../../../../shared/components/copy-button/copy-button.component';

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
    MentionModule,
    CapitalizePipe,
    TokenCountPipe,
    CopyButtonComponent,
  ],
  templateUrl: './akgent-chat.component.html',
  styleUrl: './akgent-chat.component.scss',
})
export class AkgentChatComponent implements OnInit, OnChanges {
  // The single trace scroll region (head system block + conversation). Auto
  // scroll-to-bottom targets this element so new messages stay in view.
  @ViewChild('traceScroll') traceScroll?: ElementRef<HTMLElement>;
  @Input() context$!: BehaviorSubject<any[]>;
  // akgentic-agent ADR-007 §4 — the selected agent's trimmed `AgentState.backstory`, projected
  // by the host (`AgentTabsComponent`) from the `state` PerAgentStore. Drives the
  // never-run head-block FALLBACK (a synthetic backstory row) when no
  // `LlmSystemPromptEvent` row exists yet. Optional so existing callers/tests
  // that omit it keep the event-only head block.
  @Input() backstory$?: Observable<string>;
  @Input() agentId!: string;
  @Input() agentName!: string;

  apiService: ApiService = inject(ApiService);
  utilService: UtilService = inject(UtilService);
  contextService: ContextService = inject(ContextService);
  messageService: IngestionService = inject(IngestionService);
  private config = inject(ConfigService);
  // ADR-004 §5b: component-scoped selector provided on ProcessComponent.providers
  // (Story 16-1) — resolves the same MessageLogService instance as this subtree.
  private systemPromptSelector: SystemPromptSelector = inject(
    SystemPromptSelector
  );
  // ADR-022 §Decision 5: same component-scoped selector pattern as
  // SystemPromptSelector — resolves the same team-scoped IngestionService as
  // this subtree, so the usage pill reads THIS team's per-agent totals.
  private tokenUsageSelector: TokenUsageSelector = inject(TokenUsageSelector);
  private destroyRef = inject(DestroyRef);

  context: any[] = [];

  // Mirror the main chat (user-input): Enter submits only when the
  // `userInputEnterKeySubmit` setting is enabled; Cmd/Ctrl+Enter always submit.
  userInputEnterKeySubmit: boolean = this.config.userInputEnterKeySubmit;

  /**
   * ADR-004 §5b step 2 — the head system block, derived once from the unified
   * log by `SystemPromptSelector.latestSystemPrompt$`. Bound in the template via
   * the `async` pipe (OnPush-safe; self-unsubscribes). Latest-wins MVP: the
   * trace is a flat list, so this shows the most recent rendering. The selector
   * already does latest-wins, the FR2 fallback for pre-event teams, and the
   * `dynamic_ref → name` labelling — the component consumes `row.name` /
   * `row.content` directly and renders nothing for an empty array.
   */
  systemPrompt$!: Observable<SystemPromptRow[]>;

  /**
   * akgentic-agent ADR-007 §4 — the head block actually bound in the template. Latest-wins:
   * the event-sourced `systemPrompt$` rows when present; otherwise, for a
   * never-run agent (no `LlmSystemPromptEvent` row), a single synthetic backstory
   * row built from `backstory$` (label parity with the `agent_backstory` dynamic
   * block via `systemPromptLabel`). When the first run emits its event the rows
   * become non-empty and the synthetic row is dropped — no duplicate, no flicker.
   * Rebound alongside `systemPrompt$` in `bindSystemPrompt()` on agent switch.
   */
  headRows$!: Observable<SystemPromptRow[]>;

  /**
   * ADR-022 §Decision 5 — the member-chat usage pill stream. A thin
   * `perAgent$(agentId) | async` binding (the view owns the never-run empty-state
   * via `undefined`, ADR-022 §OQ2). Re-pointed at the selected agent in
   * `bindUsage()` (called from `ngOnInit` + `ngOnChanges`), so the pill follows
   * member switches like `systemPrompt$`. OnPush-faithful by construction: bound
   * through `async`, no stored mutable copy, no manual subscription.
   */
  usage$!: Observable<AgentTokenUsage | undefined>;

  /**
   * The agent-tabs dropdown REUSES this component across member selections (it
   * lives under `*ngIf="context$.length"`, which stays truthy when switching
   * between agents that both have context), so `ngOnInit` does NOT re-run on a
   * switch. Re-bind the head system block to the newly-selected agent here, or
   * it stays pinned to the first-opened agent. The initial bind is done in
   * `ngOnInit` (which also covers unit tests that set `agentId` directly), so
   * skip the first change to avoid binding twice.
   */
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agentId'] && !changes['agentId'].firstChange) {
      this.bindSystemPrompt();
      this.bindUsage();
      // Switching members reuses this component (only the table content swaps),
      // so start FRESH: drop any carried-over follow mode and jump the trace to
      // the TOP — instantly, after the new content renders. `scrollTop = 0` is the
      // top regardless of content height.
      this.following = false;
      this.lastScrollTop = 0;
      setTimeout(() => {
        const el = this.traceScroll?.nativeElement;
        if (el) el.scrollTop = 0;
        this.updateIndicator();
      }, 0);
    }
  }

  ngOnInit(): void {
    // Initial head system block for this panel's agent (ADR-004 §5b step 2).
    // Subsequent agent switches re-bind it in ngOnChanges — see above.
    this.bindSystemPrompt();
    // Initial usage-pill stream for this panel's agent (ADR-022 §Decision 5);
    // re-bound on agent switch in ngOnChanges alongside the head block.
    this.bindUsage();

    // Subscribe to context$ for the selected agent. No scroll on enter / on every
    // message — the panel only auto-scrolls while in FOLLOW mode (see updateContext).
    this.context$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((ctx) => {
      this.isLoading = false;
      this.updateContext(ctx);
    });

    // "Auto scrolling" only applies to a RUNNING process — exit follow + refresh
    // the pill when the process stops.
    this.contextService.currentTeamRunning$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((running) => {
        if (!running) this.following = false;
        this.updateIndicator();
      });
  }

  /** Point `systemPrompt$` at the current `agentId`'s head system block and
   *  rebuild the rendered `headRows$` (event rows, else the never-run backstory
   *  fallback). The async pipe in the template resubscribes to the new stream on
   *  reassignment. */
  private bindSystemPrompt(): void {
    this.systemPrompt$ = this.systemPromptSelector.latestSystemPrompt$(
      this.agentId
    );
    // akgentic-agent ADR-007 §4: latest-wins — event rows win; otherwise synthesize a single
    // backstory row from `backstory$` (a trimmed string; `''` ⇒ no fallback).
    // `backstory$` may be absent (callers/tests that omit the input) → `of('')`.
    this.headRows$ = combineLatest([
      this.systemPrompt$,
      this.backstory$ ?? of(''),
    ]).pipe(
      map(([rows, backstory]) => {
        if (rows.length > 0) return rows;
        if (backstory.length > 0) {
          return [
            {
              type: 'system' as const,
              name: systemPromptLabel('agent_backstory'),
              content: backstory,
            },
          ];
        }
        return [];
      }),
    );
  }

  /** Point `usage$` at the current `agentId`'s token-usage stream (ADR-022
   *  §Decision 5). The async pipe in the template resubscribes on reassignment,
   *  so a member switch repoints the pill. `perAgent$` passes the store's
   *  `undefined` through for a never-run agent — the template renders the
   *  neutral empty-state (ADR-022 §OQ2). */
  private bindUsage(): void {
    this.usage$ = this.tokenUsageSelector.perAgent$(this.agentId);
  }

  /**
   * ADR-013 §3 / Epic 17 (ADR-014) — member chat target is unambiguous: this
   * panel's own agent. The `/` list is that agent's command descriptors, read
   * from `store.commands` by the panel's `agent_id` (`@Input() agentId`) and
   * mapped to dropdown items. Keying by `agent_id` (not the friendly name) is
   * the ADR-013 keying fix. Empty until a CommandsAnnouncedEvent arrives (AC-6).
   */
  get commandItems(): {
    name: string;
    description: string;
    args: CommandDescriptor['args'];
  }[] {
    const descriptors =
      this.messageService.commands.snapshot(this.agentId) ?? [];
    return descriptors
      // `_`-prefixed commands (e.g. `_expand_media_refs`) are internal, not
      // user-invocable — keep them out of the `/` dropdown.
      .filter((d) => !d.name.startsWith('_'))
      // `angular-mentions` renders a single flat list with no group headers, so
      // the best we can do is keep tool families adjacent: order by provenance
      // (`tool_card`) then command name. `.filter()` above returns a fresh
      // array, so sorting in place does not mutate the stored descriptors.
      .sort(
        (a, b) =>
          a.tool_card.localeCompare(b.tool_card) || a.name.localeCompare(b.name),
      )
      .map((d) => ({
        name: d.name,
        description: d.description,
        args: d.args,
      }));
  }

  /**
   * ADR-013 §2 — single `/` mention trigger for the member chat. Inserts
   * `/${name} ` via `selectCommand`; `allowSpace: false` closes the dropdown
   * after the command name so the user types args freely. The send path is
   * unchanged — text is sent verbatim (AC-5).
   */
  get mentionConfig() {
    return {
      mentions: [
        {
          triggerChar: '/',
          labelKey: 'name',
          allowSpace: false,
          mentionSelect: this.selectCommand,
          dropUp: true,
          maxItems: 10,
          // `angular-mentions` re-sorts every list by `labelKey` (here `name`)
          // unless told not to — that would clobber the tool-family ordering
          // `commandItems` builds. Opt out so our `tool_card`-then-name order
          // survives to the dropdown.
          disableSort: true,
          items: this.commandItems,
        },
      ],
    };
  }

  /**
   * ADR-013 §2 — insert `/${name} ` (leading slash + trailing space); does NOT
   * send. The literal text is sent verbatim on submit (AC-5).
   */
  selectCommand = (item: { name: string }) => {
    return `/${item.name} `;
  };

  /**
   * ADR-013 §2 — args hint for a command row, e.g. `<role> [name]`: required
   * in angle brackets, optional in square brackets, declared order.
   */
  commandArgsHint(args: CommandDescriptor['args']): string {
    return args
      .map((a) => (a.required ? `<${a.name}>` : `[${a.name}]`))
      .join(' ');
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
          // ADR-004 §5b step 3 — the `system-prompt` arm is intentionally gone:
          // the head system block is the SINGLE source (via systemPrompt$), so
          // the run-1 double-carry renders once. Only `user-prompt` is handled
          // here now; the system label/`dynamic_ref` logic moved to the selector.
          if (part.part_kind === 'user-prompt') {
            // Epic 29 (ADR-010 §4): the per-agent `context` store folds a
            // compaction by inserting a synthetic user-prompt prefixed
            // `CONVERSATION_SUMMARY_PREFIX`. Surface it as a clearly-labelled
            // "Summary" row (prefix stripped); every other user-prompt renders
            // unchanged.
            const isSummary =
              typeof part.content === 'string' &&
              part.content.startsWith(CONVERSATION_SUMMARY_PREFIX);
            msg.push({
              type: 'human',
              name: isSummary ? 'Summary' : 'User',
              content: isSummary
                ? part.content.slice(CONVERSATION_SUMMARY_PREFIX.length)
                : part.content,
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

    // Post-render: tail to the bottom ONLY while following; refresh the pill.
    setTimeout(() => {
      if (this.following) this.scrollToBottom();
      this.updateIndicator();
    }, 0);
  }

  userInput = '';
  isLoading = false;
  async sendMessage() {
    if (!this.contextService.currentTeamRunning$.value) return;
    this.isLoading = true;
    // Submitting enters FOLLOW mode and scrolls to the bottom (the input click no
    // longer scrolls). Subsequent replies tail via updateContext.
    this.following = true;
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
    this.scrollToBottom();
    this.updateIndicator();
  }

  /** Stable row identity for the trace table. Without it, p-table is given a
   *  brand-new array on every context update and tears down/rebuilds ALL rows,
   *  which momentarily shrinks scrollHeight, clamps scrollTop down, and fires a
   *  spurious scroll event that looks like a user scroll-up (turning off follow
   *  mid-stream). Index-based identity preserves the DOM on append. */
  trackByIndex = (index: number): number => index;

  // --- follow mode + "Messages" status pill (simplified member-chat scroll) ----
  /** FOLLOW mode: auto-scroll to the bottom on every new message. */
  following = false;
  /** Status-pill label: 'Auto scrolling' (following + running), 'Messages'
   *  (newest below the fold, not following), or null (hidden). */
  indicatorLabel: string | null = null;
  /** Last scrollTop — tells a user scroll-UP from our own smooth scroll. */
  private lastScrollTop = 0;
  /** Within this many px of the bottom counts as "at the bottom". */
  private readonly NEAR_BOTTOM = 40;

  /** Scroll handler (template `(scroll)`). A user scroll-up exits follow; the
   *  pill is re-derived from the new position. */
  onScroll(): void {
    const el = this.traceScroll?.nativeElement;
    if (!el) return;
    const movedUp = el.scrollTop < this.lastScrollTop - 2;
    this.lastScrollTop = el.scrollTop;
    // Reaching the bottom turns auto-scroll ON; scrolling up turns it off.
    if (!this.newestBelowFold()) {
      this.following = true;
    } else if (this.following && movedUp) {
      this.following = false;
    }
    this.updateIndicator();
  }

  /** Pill click — jump to the bottom and start following. */
  onFollowLatest(): void {
    this.following = true;
    this.scrollToBottom();
    this.updateIndicator();
  }

  /** True when the newest message is below the visible viewport. No spacer in the
   *  member chat, so distance-to-bottom is exact. */
  private newestBelowFold(): boolean {
    const el = this.traceScroll?.nativeElement;
    if (!el) return false;
    return el.scrollHeight - el.scrollTop - el.clientHeight > this.NEAR_BOTTOM;
  }

  /** Recompute the status-pill label. "Auto scrolling" only while the process is
   *  running; otherwise "Messages" when the newest message is below the fold. */
  private updateIndicator(): void {
    if (this.following && this.contextService.currentTeamRunning$.value) {
      this.indicatorLabel = 'Auto scrolling';
    } else {
      this.indicatorLabel = this.newestBelowFold() ? 'Messages' : null;
    }
  }

  /** Icon for the pill — a "following" glyph while auto scrolling, else a down-arrow. */
  get indicatorIcon(): string {
    return this.indicatorLabel === 'Auto scrolling' ? 'pi-sync' : 'pi-arrow-down';
  }

  private scrollToBottom(): void {
    const el = this.traceScroll?.nativeElement;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: this.scrollBehavior() });
  }

  /** Smooth by default; instant under reduced motion. */
  private scrollBehavior(): ScrollBehavior {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      ? 'auto'
      : 'smooth';
  }
}
