/**
 * Daily Report — data layer.
 *
 * - Memory (USER.md / MEMORY.md / SOUL.md) via the plugin REST door (`/memory`).
 * - Yesterday's sessions via `session.list` (light — titles/previews only).
 * - Generation via `llm.oneshot` with the LIVE session id (main model).
 *
 * Day-cache rule (MSK): one report per calendar day; produced after 08:00 MSK.
 */

import { host, type PluginRestOptions } from '@hermes/plugin-sdk'

type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>

let rest: null | Rest = null

export function bindApi(r: Rest): () => void {
  rest = r
  return () => {
    rest = null
  }
}

function call<T>(path: string, opts?: PluginRestOptions): Promise<T> {
  return rest ? rest<T>(path, opts) : Promise.reject(new Error('daily-report api not ready'))
}

// ── MSK helpers ─────────────────────────────────────────────────────────────

const MSK_MS = 3 * 60 * 60 * 1000
const DAY_MS = 24 * 60 * 60 * 1000

export function mskNow(): Date {
  return new Date(Date.now() + MSK_MS)
}

export function dayKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function isAfterEight(d: Date): boolean {
  return d.getHours() >= 8
}

// ── day-sized localStorage cache ────────────────────────────────────────────

const CACHE_KEY = 'daily-report:report'

export interface CachedReport {
  date: string
  at: number
  report: string
}

function loadCache(): CachedReport | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as CachedReport) : null
  } catch {
    return null
  }
}

function saveCache(date: string, report: string): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ date, at: Date.now(), report }))
  } catch {
    /* storage full — report won't survive a restart */
  }
}

// ── context gathering ───────────────────────────────────────────────────────

export interface MemoryContext {
  memory: string
  yesterday: string
  notes: string
}

interface MemoryEntry {
  name?: string
  content?: string
}
interface MemoryResp {
  memory: MemoryEntry[] | null
}
interface SessionRow {
  id: string
  title?: string
  preview?: string
  started_at?: number
}
interface SessionListResp {
  sessions: SessionRow[]
}

async function waitGateway(timeoutMs = 5000): Promise<boolean> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    try {
          const st = (host.state.gateway as { get(): string }).get()
          if (st === 'open') return true
        } catch {
      /* atom may not be ready — keep waiting */
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  return false
}

export async function gatherContext(): Promise<MemoryContext> {
  const today = mskNow()
  const todayStart = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - MSK_MS
  const yesterdayStart = todayStart - DAY_MS

  let memory = ''
  try {
    const resp = await call<MemoryResp>('/memory')
    if (resp?.memory?.length) {
      memory = resp.memory
        .map((e) => `\n### ${e.name ?? 'Memory'}\n${e.content ?? ''}`)
        .join('\n')
        .slice(0, 6000)
    }
  } catch {
    memory = ''
  }

  let yesterday = ''
  let notes = ''
  try {
    await waitGateway()
    const resp = await host.request<SessionListResp>('session.list', { limit: 200 })
    const rows = (resp?.sessions || []).filter((row) => {
      let ts = row.started_at || 0
      if (ts && ts < 1e12) ts *= 1000 // seconds -> ms
      return ts >= yesterdayStart && ts < todayStart
    })
    yesterday = rows
      .map((row) => `- ${row.title || '(без темы)'}${row.preview ? ` — «${row.preview}»` : ''}`)
      .join('\n')
  } catch (e) {
    notes = `Не удалось загрузить сессии: ${e instanceof Error ? e.message : String(e)}`
  }

  return { memory, yesterday, notes }
}

// ── generation ──────────────────────────────────────────────────────────────

export function buildPrompt(ctx: MemoryContext): { instructions: string; input: string } {
  const d = mskNow()
  const dateStr = `${d.getDate()}.${d.getMonth() + 1}.${d.getFullYear()}`
  const instructions = [
    'Ты пишешь личный ежедневный отчёт для владельца Hermes. Язык: русский.',
    'Стиль: честный, прямой, БЕЗ подхалимажа. Оценивай строго по данным.',
    'ЖЁСТКИЕ ПРАВИЛА:',
    '1. Если данных за вчера НЕТ — в разделе «Вчера» пиши ровно одну строку «Нет данных», не выдумывай.',
    '2. НЕ пиши общие фразы-заглушки в плане («восстановить контекст», «аудит процессов», «формирование бэклога»).',
    '3. «План на сегодня» — дай 3–5 КОНКРЕТНЫХ, выполнимых шагов, выведенных из проектов, целей и планов в памяти ниже и из вчерашней активности. Например: «завершить X и запустить Y / разобрать последний анализ RobloxZavod / закоммитить незакоммиченные правки в форке».',
    '4. Если данных мало — всё равно дай минимальный разумный план на основе того, что есть (проекты из памяти), а НЕ отказ «не хватает данных». Факты не выдумывай, но гипотезы о шагах допустимы и помечай как предложения.',
    '5. Приоритет одного фокуса: в конце плана выдели главный фокус дня (1 строка).',
    'Компактно: короткие разделы, без воды.',
    '',
    `Формат markdown:\n# Отчёт на ${dateStr}\n## Вчера — что делалось\n## План на сегодня\n## Риски / незакрытое\n## Главное (1-2 предложения)`,
    ''
  ].join('\n')

  const input = [
    'ЛИЧНЫЙ КОНТЕКСТ (память Hermes):',
    ctx.memory || '— пусто —',
    '',
    'ВЧЕРАШНИЕ СЕССИИ (только заголовки и превьюи):',
    ctx.yesterday || '— нет записей —',
    ctx.notes ? `\nПРИМЕЧАНИЕ: ${ctx.notes}` : '',
    ''
  ].join('\n')

  return { instructions, input }
}

export async function generateReport(sessionId: string, ctx: MemoryContext): Promise<string> {
  const { instructions, input } = buildPrompt(ctx)
  const res = await host.request<{ output?: string; text?: string }>('llm.oneshot', {
    session_id: sessionId || undefined,
    instructions,
    input,
    task: 'reasoning',
    max_tokens: 2400,
    temperature: 0.4
  })
  const text = (res?.output || res?.text || '').trim()
  if (!text) throw new Error('Модель вернула пустой ответ')
  return text
}

// ── public day-cache API (used by the panel) ────────────────────────────────

export function statusForToday(): { loaded: CachedReport | null; kind: 'fresh' | 'stale' | 'none' | 'due' } {
  const today = mskNow()
  const key = dayKey(today)
  const cached = loadCache()
  if (cached?.date === key) {
    return { loaded: cached, kind: 'fresh' }
  }
  if (isAfterEight(today)) {
    return { loaded: cached, kind: 'due' }
  }
  return { loaded: cached, kind: cached?.report ? 'stale' : 'none' }
}

export function storeToday(report: string): void {
  saveCache(dayKey(mskNow()), report)
}