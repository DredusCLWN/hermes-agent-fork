/**
 * Daily Report plugin — SDK plugin (backend is only the bundled `/memory` route).
 *
 * Registers a `/daily` route + a sidebar nav row (after Kanban) + palette command.
 * The panel shows a daily agent report: what was done yesterday (from the
 * session list) and what to do today + risks — grounded in the user's REAL
 * memory (USER.md / MEMORY.md / SOUL.md) and their recent sessions.
 * Honest, no flattery. Includes a live countdown to the next auto-rebuild.
 */

import {
  Badge,
  Button,
  cn,
  Codicon,
  EmptyState,
  ErrorState,
  type HermesPlugin,
  host,
  icons,
  type PaletteContribution,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'

import {
  bindApi,
  gatherContext,
  generateReport,
  type MemoryContext,
  mskNow,
  statusForToday,
  storeToday
} from './api'

function todayLabel(): string {
  const d = mskNow()
  const p = (n: number) => String(n).padStart(2, '0')

  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// ── countdown ring to the next 08:00 MSK rebuild ────────────────────────────

const MSK_MS = 3 * 60 * 60 * 1000

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function CountdownRing() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)

    return () => clearInterval(t)
  }, [])

  const r = new Date(now + MSK_MS)
  const t = new Date(r.getFullYear(), r.getMonth(), r.getDate(), 8, 0, 0, 0)

  if (r.getTime() >= t.getTime()) {t.setDate(t.getDate() + 1)}
  const remaining = t.getTime() - r.getTime()
  const dayFrac = 1 - remaining / (24 * 60 * 60 * 1000)

  const h = Math.floor(remaining / 3600000)
  const m = Math.floor((remaining % 3600000) / 60000)
  const s = Math.floor((remaining % 60000) / 1000)
  const R = 13
  const C = 2 * Math.PI * R

  return (
    <div className="flex items-center gap-2.5 text-xs">
      <svg className="shrink-0 -rotate-90" height="34" viewBox="0 0 34 34" width="34">
        <circle cx="17" cy="17" fill="none" r={R} stroke="var(--dt-stroke-tertiary)" strokeWidth="3.5" />
        <circle
          cx="17"
          cy="17"
          fill="none"
          r={R}
          stroke="var(--ui-accent)"
          strokeDasharray={C}
          strokeDashoffset={(1 - dayFrac) * C}
          strokeLinecap="round"
          strokeWidth="3.5"
        />
      </svg>
      <span className="tabular-nums text-muted-foreground">
        до след. отчёта <span className="text-foreground">{pad2(h)}:{pad2(m)}:{pad2(s)}</span>
      </span>
    </div>
  )
}

// ── panel ───────────────────────────────────────────────────────────────────

function ReportPanel() {
  const qc = useQueryClient()
  const sessionId = useValue(host.state.activeSessionId)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const view = statusForToday()
  const cached = view.loaded
  const fresh = view.kind === 'fresh'
  const dueToday = view.kind === 'due'

  async function onRefresh(): Promise<void> {
    if (generating) {return}

    // До 08:00 МСК нового дня не перегенерируем (показываем вчерашний).
    if (!fresh && !dueToday) {return}
    setGenerating(true)
    setError(null)

    try {
      const ctx: MemoryContext = await gatherContext()
      const report = await generateReport(sessionId || '', ctx)
      storeToday(report)
      void qc.invalidateQueries({ queryKey: ['daily-report'] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
    }
  }

  const title = fresh
    ? `Отчёт · ${todayLabel()}`
    : cached
      ? `Отчёт (вчерашний) · ${cached.date}`
      : 'Отчёт на день'

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Codicon className="text-muted-foreground" name="calendar" />
          {title}
          {!fresh && cached && <Badge variant="muted">вчера · до 08:00 МСК</Badge>}
          {fresh && <Badge>сегодня</Badge>}
        </div>
        <Button
          className={cn('shrink-0')}
          disabled={generating || view.kind === 'stale'}
          onClick={onRefresh}
          size="sm"
          title={view.kind === 'stale' ? 'Новый отчёт только после 08:00 МСК' : 'Сгенерировать / обновить отчёт'}
          variant="ghost"
        >
          {generating ? <icons.Loader2Icon className="animate-spin" size={14} /> : <icons.RefreshCwIcon />}
          Обновить
        </Button>
      </header>

      <div className="-mt-1 flex justify-end">
        <CountdownRing />
      </div>

      {generating && <p className="text-xs text-muted-foreground">Собираю контекст из памяти и вчерашних сессий…</p>}
      {error && <ErrorState description={error} title="Не удалось собрать отчёт" />}

      {!error && cached?.report && (
        <article className="max-w-[860px] flex-1 select-text overflow-y-auto rounded-lg border bg-muted/40 p-4 font-sans text-sm leading-relaxed whitespace-pre-wrap text-foreground">
          {cached.report}
        </article>
      )}

      {!error && !cached?.report && !generating && (
        <EmptyState
          description="Сгенерируется автоматически после 08:00 МСК или по кнопке «Обновить»."
          title="Отчёт на сегодня ещё не готов"
        />
      )}
    </div>
  )
}

// ── registration ────────────────────────────────────────────────────────────

const plugin: HermesPlugin = {
  id: 'daily-report',
  name: 'Daily Report',
  defaultEnabled: true,
  register(ctx) {
    ctx.onDispose(bindApi(ctx.rest))
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/daily' } satisfies RouteContribution,
        render: () => <ReportPanel />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 44,
        data: { codicon: 'calendar', label: 'Daily Report', path: '/daily' } satisfies SidebarNavContribution
      },
      {
        id: 'open',
        area: 'palette',
        data: {
          id: 'daily-report.open',
          label: 'Daily Report: Открыть отчёт на день',
          keywords: ['report', 'daily', 'отчёт', 'план', 'daily-report'],
          run: () => host.navigate('/daily')
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin