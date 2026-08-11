/**
 * Graphify data layer — proxies all requests through ctx.rest (the desktop's
 * namespace-scoped REST door to the Hermes backend), same pattern as Kanban.
 */

import { type PluginRestOptions } from '@hermes/plugin-sdk'

type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>

let rest: null | Rest = null

export function bindApi(r: Rest): () => void {
  rest = r

  return () => { rest = null }
}

function call<T>(path: string, opts?: PluginRestOptions): Promise<T> {
  return rest ? rest<T>(path, opts) : Promise.reject(new Error('graphify api not ready'))
}

// ── types ────────────────────────────────────────────────────────────────────

export interface GraphStatus {
  status: string
  progress: number
  error: string | null
  node_count: number
  edge_count: number
  community_count: number
  graph_exists: boolean
  cwd?: string | null
  mode?: string
  backend?: string | null
  model?: string | null
  warnings?: string | null
}

export interface GraphNode {
  id: string
  label?: string
  type?: string
  /** Backend (graphify AST) field name for `type`; normalized away on load. */
  file_type?: string
  file?: string
  /** Backend field name for `file`; normalized away on load. */
  source_file?: string
  community?: number
  cluster?: number
  community_label?: string
  degree_centrality?: number
  betweenness_centrality?: number
  /** Local degree, computed by the renderer from the visible link set. */
  degree?: number
}

export interface GraphLink {
  source: string
  target: string
  kind?: string
  relation?: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export interface BuildRequest {
  mode?: string
  backend?: string
  model?: string
  cwd?: string
}

// ── API calls ────────────────────────────────────────────────────────────────

export const fetchStatus = () => call<GraphStatus>('/status')

export const fetchGraph = (limit?: number) =>
  call<GraphData>(`/graph.json${limit ? `?limit=${limit}` : ''}`)

export const fetchSubgraph = (node: string) =>
  call<GraphData>(`/graph.json?node=${encodeURIComponent(node)}`)

export const buildGraph = (body: BuildRequest = {}) =>
  call<{ task_id: string; status: string }>('/build', { method: 'POST', body })

export const cancelBuild = () =>
  call<{ status: string }>('/cancel', { method: 'POST' })

export const enhanceGraph = (model: string) =>
  call<{ task_id: string; status: string }>('/enhance', { method: 'POST', body: { model } })

export const findPath = (from: string, to: string) =>
  call<{ from: string; to: string; path: string[]; error?: string }>(
    `/path?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`
  )

export const queryGraph = (q: string) =>
  call<{ query: string; results: unknown[]; error?: string }>(
    `/query?q=${encodeURIComponent(q)}`
  )

export const fetchModels = (backend: string) =>
  call<{ backend: string; models: string[]; live: boolean; error?: string }>(
    `/models?backend=${encodeURIComponent(backend)}`
  )
