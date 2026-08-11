import {
  ActionBarPrimitive,
  BranchPickerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  useAuiState,
  useMessageRuntime
} from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { type FC, useCallback, useMemo } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { ChangedFilesCard } from '@/components/assistant-ui/thread/changed-files-card'
import {
  contentHasVisibleText,
  messageContentText,
  pickPrimaryPreviewTarget
} from '@/components/assistant-ui/thread/content'
import { MESSAGE_PARTS_COMPONENTS } from '@/components/assistant-ui/thread/message-parts'
import { ResponseLoadingIndicator, StreamStallIndicator } from '@/components/assistant-ui/thread/status'
import { formatMessageTimestamp } from '@/components/assistant-ui/thread/timestamp'
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button'
import { PreviewAttachment } from '@/components/chat/preview-attachment'
import { Codicon } from '@/components/ui/codicon'
import { CopyButton } from '@/components/ui/copy-button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { BarChart3, GitForkIcon, RefreshCwIcon, XIcon, Zap } from '@/lib/icons'
import { extractPreviewTargets } from '@/lib/preview-targets'
import { formatAgo } from '@/lib/time'
import { useEnterAnimation } from '@/lib/use-enter-animation'
import type { UsageStats } from '@/types/hermes'

// Stable empty identity for the settled-parts selector — a fresh [] per render
// would re-derive the changed-files card on every message re-render.
const EMPTY_PARTS: readonly unknown[] = []

interface MessageActionProps {
  messageId: string
  /** Lazy accessor — reads the live message text at action time. Passing the
   *  text itself as a prop forces the whole footer to re-render on every
   *  streaming delta flush (the text changes ~30×/s), which profiling showed
   *  was a large slice of per-token script time on long transcripts. */
  getMessageText: () => string
  onBranchInNewChat?: (messageId: string) => void
}

export const AssistantMessage: FC<{
  onBranchInNewChat?: (messageId: string) => void
  onDismissError?: (messageId: string) => void
}> = ({ onBranchInNewChat, onDismissError }) => {
  const messageId = useAuiState(s => s.message.id)
  const messageRuntime = useMessageRuntime()
  const { t } = useI18n()

  // PERF: this component must NOT subscribe to the streaming text. Every
  // selector here returns a value that stays referentially stable across
  // token flushes (booleans, status strings, '' while running), so the
  // 30 Hz delta stream only re-renders the markdown part and the tiny
  // StreamStallIndicator leaf — not the footer/preview/root subtree.
  const messageStatus = useAuiState(s => s.message.status?.type)
  const isRunning = messageStatus === 'running'
  const isPlaceholder = useAuiState(s => s.message.status?.type === 'running' && s.message.content.length === 0)
  const hasVisibleText = useAuiState(s => contentHasVisibleText(s.message.content))
  // Sealed mid-turn commentary keeps its text but not the footer, so a
  // tool-heavy turn doesn't grow a copy/refresh bar per paragraph (see
  // ChatMessage.interim).
  const isInterim = useAuiState(s => s.message.metadata?.custom?.interim === true)

  // The thinking/stall indicator belongs to the TAIL of the thread, period. A
  // stale pending bubble mid-transcript (a turn that ended without its settle
  // event, a steer race) must never show one — a spinner above a later user
  // message reads as the agent answering out of order. Booleans are stable
  // across token flushes, so this selector adds no streaming re-renders.
  const isLastMessage = useAuiState(s => s.thread.messages[s.thread.messages.length - 1]?.id === s.message.id)

  // Preview targets only materialize once the turn completes — while running
  // the selector returns '' (stable), so per-token flushes skip the regex
  // scan and the re-render it would cause.
  const completedText = useAuiState(s =>
    s.message.status?.type === 'running' ? '' : messageContentText(s.message.content)
  )

  const previewTargets = useMemo(() => {
    if (!completedText || !/(https?:\/\/|file:\/\/)/i.test(completedText)) {
      return []
    }

    return pickPrimaryPreviewTarget(extractPreviewTargets(completedText))
  }, [completedText])

  const getMessageText = useCallback(() => messageContentText(messageRuntime.getState().content), [messageRuntime])

  // Cursor's changed-files card only appears once the turn settles: while the
  // agent is still editing, the tool rows narrate each patch and a card that
  // grew a row per write would thrash the transcript. `[]` while running keeps
  // this selector referentially stable across the 30 Hz delta stream.
  //
  // It also only rides the LAST turn. The card is a "here's what just landed"
  // summary, not a per-turn artifact: leaving one behind on every reply would
  // stack a wall of stale cards down the transcript. Sending the next message
  // retires it — the working tree it describes is already history by then.
  const settledParts = useAuiState(s => {
    const isLastMessage = s.thread.messages[s.thread.messages.length - 1]?.id === s.message.id

    return s.message.status?.type === 'running' || !isLastMessage ? EMPTY_PARTS : s.message.parts
  })

  const enterRef = useEnterAnimation(isRunning, `assistant-message:${messageId}`)

  return (
    <MessagePrimitive.Root
      className="group flex w-full min-w-0 max-w-full flex-col gap-0 self-start overflow-hidden"
      data-role="assistant"
      data-slot="aui_assistant-message-root"
      data-streaming={isRunning ? 'true' : undefined}
      ref={enterRef}
    >
      <div
        className="wrap-anywhere min-w-0 max-w-full overflow-hidden text-pretty text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground"
        data-slot="aui_assistant-message-content"
      >
        {/* Todos render in the composer status stack now, not inline. */}
        <MessagePrimitive.Parts components={MESSAGE_PARTS_COMPONENTS} />
        {isLastMessage && (isPlaceholder ? <ResponseLoadingIndicator /> : isRunning && <StreamStallIndicator />)}
        {previewTargets.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {previewTargets.map(target => (
              <PreviewAttachment key={target} source="explicit-link" target={target} />
            ))}
          </div>
        )}
        <MessagePrimitive.Error>
          <ErrorPrimitive.Root
            className="mt-1.5 flex items-start gap-1.5 text-[0.78rem] leading-5 text-[color-mix(in_srgb,var(--dt-destructive)_78%,var(--ui-text-secondary))]"
            role="alert"
          >
            <ErrorPrimitive.Message className="min-w-0 flex-1" />
            {onDismissError && (
              <TooltipIconButton
                className="-my-0.5 shrink-0 text-current opacity-70 hover:opacity-100"
                onClick={() => onDismissError(messageId)}
                side="top"
                tooltip={t.assistant.thread.dismissError}
              >
                <XIcon className="size-3.5" />
              </TooltipIconButton>
            )}
          </ErrorPrimitive.Root>
        </MessagePrimitive.Error>
      </div>
      {hasVisibleText && !isInterim && (
        <AssistantFooter getMessageText={getMessageText} messageId={messageId} onBranchInNewChat={onBranchInNewChat} />
      )}
      {/* Last thing in the turn — under the action bar, the way Cursor ends a
          turn on its summary rather than burying it above the controls. */}
      <ChangedFilesCard parts={settledParts} />
    </MessagePrimitive.Root>
  )
}

function _fmtTokens(n: number): string {
  if (n >= 1_000_000) {return `${(n / 1_000_000).toFixed(1)}M`}

  if (n >= 1000) {return `${(n / 1000).toFixed(1)}k`}

  return String(n)
}

function _fmtCost(usd: number): string {
  if (usd <= 0) {return '$0'}

  if (usd >= 0.01) {return `$${usd.toFixed(4)}`}

  if (usd >= 0.0001) {return `$${usd.toFixed(6)}`}

  return `$${usd.toExponential(2)}`
}

const TokenSavings: FC = () => {
  const view = useSessionView()
  const usage = useStore(view.$usage) as UsageStats | null

  if (!usage?.savings || usage.savings <= 0) {return null}

  const breakdown = usage.savings_breakdown ?? {}

  const items = [
    { key: 'cache', label: 'Cache', value: breakdown.cache ?? 0 },
    { key: 'caveman', label: 'Caveman', value: breakdown.caveman ?? 0 },
    { key: 'ponytail', label: 'Ponytail', value: breakdown.ponytail ?? 0 },
    { key: 'compression', label: 'Compression', value: breakdown.compression ?? 0 },
    { key: 'compression_llm_cost', label: 'Compression cost', value: -(breakdown.compression_llm_cost ?? 0) },
    { key: 'terminal_compression', label: 'Terminal compress', value: breakdown.terminal_compression ?? 0 },
    { key: 'micro_compact', label: 'Micro-compact', value: breakdown.micro_compact ?? 0 },
    { key: 'cleanup_dedup', label: 'Tool dedup', value: breakdown.cleanup_dedup ?? 0 },
    { key: 'cleanup_whitespace', label: 'Whitespace', value: breakdown.cleanup_whitespace ?? 0 },
    { key: 'cleanup_json_compact', label: 'JSON compact', value: breakdown.cleanup_json_compact ?? 0 },
    { key: 'cleanup_json_table', label: 'JSON table', value: breakdown.cleanup_json_table ?? 0 },
    { key: 'cleanup_repeated_lines', label: 'Repeated lines', value: breakdown.cleanup_repeated_lines ?? 0 },
    { key: 'cleanup_reasoning_strip', label: 'Reasoning strip', value: breakdown.cleanup_reasoning_strip ?? 0 },
    { key: 'cleanup_read_file_dedup', label: 'Read-file dedup', value: breakdown.cleanup_read_file_dedup ?? 0 },
    { key: 'cleanup_empty_strip', label: 'Empty strip', value: breakdown.cleanup_empty_strip ?? 0 },
    { key: 'cleanup_terminal_truncate', label: 'Terminal truncate', value: breakdown.cleanup_terminal_truncate ?? 0 },
    { key: 'cleanup_json_aggressive', label: 'JSON aggressive', value: breakdown.cleanup_json_aggressive ?? 0 },
  ]

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          className="flex shrink-0 items-center gap-1 py-1.5 pl-1 text-[0.6875rem] tabular-nums text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
          type="button"
        >
          <Zap className="size-3 text-(--ui-warm)" />
          <span>{_fmtTokens(usage.savings)} saved</span>
        </button>
      </TooltipTrigger>
      <TooltipContent align="start" className="w-72 p-3" rich side="top" sideOffset={8}>
        <p className="mb-2 text-[0.6875rem] font-medium text-foreground">
          Token savings
        </p>
        <ul className="flex flex-col gap-0.5">
          {items.map(item => (
            <li className="flex items-center justify-between gap-3 text-[0.6875rem]" key={item.key}>
              <span className="shrink truncate text-muted-foreground">{item.label}</span>
              <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(item.value)}</span>
            </li>
          ))}
        </ul>
        <div className="mt-2 border-t border-(--ui-stroke-tertiary) pt-1.5 text-[0.6875rem] font-medium text-foreground">
          Total: {_fmtTokens(usage.savings)}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

const TokenSpending: FC = () => {
  const view = useSessionView()
  const usage = useStore(view.$usage) as UsageStats | null

  if (!usage || !usage.total) {return null}

  const hasContext = usage.context_used != null && usage.context_max != null && usage.context_max > 0
  const reasoningInOutput = (usage.reasoning ?? 0) > 0 && (usage.input + usage.output) > usage.total

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          className="flex shrink-0 items-center gap-1 py-1.5 pl-1 text-[0.6875rem] tabular-nums text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
          type="button"
        >
          <BarChart3 className="size-3 text-(--ui-midground)" />
          <span>{_fmtTokens(usage.total)}</span>
        </button>
      </TooltipTrigger>
      <TooltipContent align="start" className="w-80 p-3" rich side="top" sideOffset={8}>
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <p className="text-[0.6875rem] font-medium text-foreground">Token spending</p>
          {usage.model ? (
            <span className="shrink-0 text-[0.6875rem] text-muted-foreground">{usage.model}</span>
          ) : null}
        </div>

        {/* Core token breakdown */}
        <ul className="flex flex-col gap-0.5">
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Input (prompt)</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(usage.input)}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Output (completion)</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(usage.output)}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">
              Reasoning{reasoningInOutput ? ' (incl. in output)' : ''}
            </span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(usage.reasoning ?? 0)}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Cache read</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(usage.cache_read ?? 0)}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Cache write</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtTokens(usage.cache_write ?? 0)}</span>
          </li>
        </ul>

        <div className="mt-1.5 border-t border-(--ui-stroke-tertiary) pt-1.5 text-[0.6875rem] font-medium text-foreground">
          Total: {_fmtTokens(usage.total)}
        </div>

        {/* Meta: calls, cost, context, compressions, subagents */}
        <div className="mt-2 flex flex-col gap-0.5 border-t border-(--ui-stroke-tertiary) pt-2">
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">API calls</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.calls}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">
              Cost{usage.cost_status && usage.cost_status !== 'unknown' ? ` (${usage.cost_status})` : ''}
            </span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{_fmtCost(usage.cost_usd ?? 0)}</span>
          </li>
          {hasContext ? (
            <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
              <span className="shrink truncate text-muted-foreground">Context window</span>
              <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">
                {_fmtTokens(usage.context_used ?? 0)}/{_fmtTokens(usage.context_max ?? 0)} ({usage.context_percent ?? 0}%)
              </span>
            </li>
          ) : (
            <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
              <span className="shrink truncate text-muted-foreground">Context window</span>
              <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">—</span>
            </li>
          )}
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Compressions</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.compressions ?? 0}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Active subagents</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.active_subagents ?? 0}</span>
          </li>
        </div>

        {/* Per-turn averages */}
        <div className="mt-2 flex flex-col gap-0.5 border-t border-(--ui-stroke-tertiary) pt-2">
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Avg input / call</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.calls > 0 ? _fmtTokens(Math.round(usage.input / usage.calls)) : '—'}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Avg output / call</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.calls > 0 ? _fmtTokens(Math.round(usage.output / usage.calls)) : '—'}</span>
          </li>
          <li className="flex items-center justify-between gap-3 text-[0.6875rem]">
            <span className="shrink truncate text-muted-foreground">Avg total / call</span>
            <span className="shrink-0 whitespace-nowrap tabular-nums text-foreground">{usage.calls > 0 ? _fmtTokens(Math.round(usage.total / usage.calls)) : '—'}</span>
          </li>
        </div>

      </TooltipContent>
    </Tooltip>
  )
}

const AssistantActionBar: FC<MessageActionProps> = ({ messageId, getMessageText, onBranchInNewChat }) => {
  const { t } = useI18n()
  const copy = t.assistant.thread

  return (
    <div className="relative flex w-full shrink-0 items-center justify-between gap-1.5">
      <div className="flex items-center gap-2">
        <TokenSpending />
        <TokenSavings />
      </div>
      <ActionBarPrimitive.Root
        className={
          // NOTE: intentionally NOT `hideWhenRunning`. That prop unmounts the
          // bar while the thread streams, which collapses every completed
          // assistant message's footer by this bar's height and shifts the
          // whole conversation when the turn resolves. The bar is already
          // invisible by default (opacity-0 + pointer-events-none, reveals on
          // hover), so keeping it mounted reserves stable layout height with
          // no visual change during streaming.
          'relative flex flex-row items-center justify-end gap-1.5 py-1.5 opacity-0 pointer-events-none group-hover:pointer-events-auto group-hover:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100'
        }
        data-slot="aui_msg-actions"
      >
        <MessageAge />
        {onBranchInNewChat && (
          <TooltipIconButton
            onClick={() => {
              triggerHaptic('selection')
              onBranchInNewChat(messageId)
            }}
            tooltip={copy.branchNewChat}
          >
            <GitForkIcon className="size-3.5" />
          </TooltipIconButton>
        )}
        <CopyButton appearance="icon" buttonSize="icon" label={copy.copy} text={getMessageText} />
        <ActionBarPrimitive.Reload asChild>
          <TooltipIconButton onClick={() => triggerHaptic('submit')} tooltip={copy.refresh}>
            <RefreshCwIcon className="size-3.5" />
          </TooltipIconButton>
        </ActionBarPrimitive.Reload>
      </ActionBarPrimitive.Root>
    </div>
  )
}

const MessageAge: FC = () => {
  const { t } = useI18n()
  const createdAt = useAuiState(s => s.message.createdAt)
  const date = createdAt ? new Date(createdAt) : null

  if (!date || Number.isNaN(date.getTime())) {
    return null
  }

  // Compact "2h ago" (shared util) with the absolute time on hover.
  return (
    <span
      className="px-0.5 text-[0.6875rem] tabular-nums text-muted-foreground"
      title={formatMessageTimestamp(date, t.assistant.thread) || undefined}
    >
      {formatAgo(date.getTime(), t.agents)}
    </span>
  )
}

const AssistantFooter: FC<MessageActionProps> = props => (
  <div className="flex min-h-6 flex-col items-end gap-1 pr-(--message-text-indent) pl-(--message-text-indent)">
    <BranchPickerPrimitive.Root
      className="inline-flex h-6 items-center gap-1 text-xs text-muted-foreground"
      hideWhenSingleBranch
    >
      <BranchPickerPrimitive.Previous className="grid size-6 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-default disabled:opacity-35">
        <Codicon name="chevron-left" size="0.875rem" />
      </BranchPickerPrimitive.Previous>
      <span className="tabular-nums">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next className="grid size-6 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-default disabled:opacity-35">
        <Codicon name="chevron-right" size="0.875rem" />
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
    <AssistantActionBar {...props} />
  </div>
)
