/**
 * GraphCanvas — interactive code dependency graph page.
 *
 * Fetches graph data from the graphify plugin REST API and renders
 * an interactive vis-network graph with filters, search, and node inspection.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cn,
  Button,
  Input,
  Badge,
  Codicon,
  StatusDot,
  host,
  useValue,
} from '@hermes/plugin-sdk'

import {
  fetchStatus, fetchGraph,
  buildGraph, cancelBuild, enhanceGraph, fetchModels,
  type GraphNode, type GraphLink, type GraphStatus,
} from './api'

export function GraphCanvas() {
  // Live cwd from the focused chat session — switches automatically
  // when the user changes projects/chats in the sidebar. Falls back to
  // the global cwd for drafts/detached views.
  const activeCwd = useValue(host.state.fileTreeCwd)
  // Created projects (named, multi-folder workspaces) for the project selector.
  const projects = useValue(host.state.projects)
  // When the user picks a project from the selector, it overrides activeCwd.
  const [selectedProjectCwd, setSelectedProjectCwd] = useState<string | null>(null)
  const effectiveCwd = selectedProjectCwd ?? activeCwd
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [links, setLinks] = useState<GraphLink[]>([])
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [status, setStatus] = useState<GraphStatus>({
    status: 'loading', progress: 0, error: null,
    node_count: 0, edge_count: 0, community_count: 0, graph_exists: false,
  })
  const [visFailed, setVisFailed] = useState(false)
  const [backend, setBackend] = useState('ollama')
  const [modelName, setModelName] = useState('')
  const [modelList, setModelList] = useState<string[]>([])
  const [modelsLive, setModelsLive] = useState(false)
  const [modelsError, setModelsError] = useState('')
  const [modelsReady, setModelsReady] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [renderingProgress, setRenderingProgress] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<any>(null)
  const nodesDataSetRef = useRef<any>(null)
  const edgesDataSetRef = useRef<any>(null)
  const allNodesRef = useRef<GraphNode[]>([])
  const themeColorsRef = useRef<any>(null)
  const autoBuildTriggered = useRef(false)
  const renderTimerRef = useRef<any>(null)

  // Fetch models when backend changes
  useEffect(() => {
    if (!backend) return
    setModelsReady(false)
    setModelList([])
    setModelsError('')
    fetchModels(backend)
      .then((data) => {
        setModelList(data.models || [])
        setModelsLive(data.live || false)
        setModelsError(data.error || '')
        if (data.models?.length && !modelName) {
          setModelName(data.models[0])
        }
      })
      .catch(() => {})
      .finally(() => setModelsReady(true))
  }, [backend])

  // Status polling — restarts whenever build or enhance starts.
  useEffect(() => {
    fetchStatus().then(setStatus).catch(() => {})
    if (status.status !== 'building' && status.status !== 'enhancing') return

    const timer = setInterval(async () => {
      try {
        const data = await fetchStatus()
        setStatus(data)
        if (data.status !== 'building' && data.status !== 'enhancing') clearInterval(timer)
      } catch {}
    }, 2000)
    return () => clearInterval(timer)
  }, [status.status])

  // Auto-build on first visit or when the active project (cwd) changes.
  // Waits for model list to load so we send the correct model name,
  // not graphify's default (which may not exist in local Ollama).
  // Each project gets exactly one auto-build; switching chats/projects
  // triggers a fresh build for the new cwd.
  const lastBuiltCwd = useRef<string>('')
  useEffect(() => {
    if (!modelsReady || !effectiveCwd) return
    if (autoBuildTriggered.current && lastBuiltCwd.current === effectiveCwd) return

    autoBuildTriggered.current = true
    lastBuiltCwd.current = effectiveCwd

    // Reset graph state for the new project
    setNodes([])
    setLinks([])
    setSelected(null)

    const payload: Record<string, string> = { mode: 'ast', backend, cwd: effectiveCwd }
    if (modelName) payload.model = modelName
    buildGraph(payload).catch(() => {})
  }, [effectiveCwd, modelsReady, modelName, backend])

  // Load graph when ready
  const loadGraph = useCallback(async () => {
    try {
      const data = await fetchGraph(500)
      const rawNodes = data.nodes || []
      // The backend returns raw graphify nodes keyed file_type/source_file.
      // Normalize to the shape the UI reads (type/file/degree) so tooltips,
      // node sizes and the inspector show real values instead of blanks.
      const degreeByNode = new Map<string, number>()
      for (const l of data.links || []) {
        degreeByNode.set(l.source, (degreeByNode.get(l.source) ?? 0) + 1)
        degreeByNode.set(l.target, (degreeByNode.get(l.target) ?? 0) + 1)
      }
      const normNodes = rawNodes.map((n: GraphNode) => ({
        ...n,
        type: n.type ?? n.file_type,
        file: n.file ?? n.source_file,
        degree: degreeByNode.get(n.id) ?? 0,
      }))
      setNodes(normNodes)
      setLinks(data.links || [])
    } catch {}
  }, [])

  // Reset auto-build flag on error so a retry can fire.
  useEffect(() => {
    if (status.status === 'error' && autoBuildTriggered.current) {
      autoBuildTriggered.current = false
    }
  }, [status.status])

  // Load graph when ready (after build or enhance completes)
  useEffect(() => {
    if (status.status === 'ready') {
      loadGraph()
    }
  }, [status.status])

  // Create vis-network ONCE with empty DataSets. Nodes are added
  // incrementally in a separate effect to animate "building node by node".
  // vis-network creates its own canvas/DOM — React must not manage children
  // of the container div (causes removeChild crash).
  useEffect(() => {
    if (!containerRef.current) return

    function initNetwork() {
      const vis = (window as any).vis
      if (!vis?.Network) { setVisFailed(true); return }

      // Read theme tokens once
      const styles = getComputedStyle(document.documentElement)
      const isDark = document.documentElement.classList.contains('dark')
      const textPrimary = styles.getPropertyValue('--ui-text-primary').trim() || (isDark ? '#e0e0e0' : '#17171a')
      const accent = styles.getPropertyValue('--ui-accent').trim() || '#0053fd'
      const red = styles.getPropertyValue('--ui-red').trim() || '#cf2d56'
      const green = styles.getPropertyValue('--ui-green').trim() || '#1f8a65'
      const yellow = styles.getPropertyValue('--ui-yellow').trim() || '#c08532'
      const purple = styles.getPropertyValue('--ui-purple').trim() || '#9e94d5'
      const cyan = styles.getPropertyValue('--ui-cyan').trim() || '#4c7f8c'
      const orange = styles.getPropertyValue('--ui-orange').trim() || '#db704b'
      const blue = styles.getPropertyValue('--ui-blue').trim() || '#0053fd'

      // Gray-white edge color: light gray in dark theme, medium gray in light
      const edgeColor = isDark ? 'rgba(190, 190, 190, 0.35)' : 'rgba(90, 90, 90, 0.45)'
      const edgeFontColor = isDark ? 'rgba(170, 170, 170, 0.6)' : 'rgba(80, 80, 80, 0.6)'

      themeColorsRef.current = {
        textPrimary,
        edgeColor,
        edgeFontColor,
        edgeHighlight: red,
        accent,
        palette: [accent, blue, green, purple, cyan, orange, yellow, red],
      }

      nodesDataSetRef.current = new vis.DataSet([])
      edgesDataSetRef.current = new vis.DataSet([])

      networkRef.current?.destroy()
      networkRef.current = new vis.Network(containerRef.current, {
        nodes: nodesDataSetRef.current,
        edges: edgesDataSetRef.current,
      }, {
        layout: { improvedLayout: true },
        physics: {
          barnesHut: {
            gravitationalConstant: -2000,
            centralGravity: 0.1,
            springLength: 150,
            springConstant: 0.05,
            damping: 0.4,
          },
          stabilization: { enabled: false },
        },
        interaction: { hover: true, tooltipDelay: 200 },
        nodes: { shape: 'dot', borderWidth: 2 },
        edges: { smooth: { type: 'continuous' }, width: 1 },
      })

      networkRef.current.on('click', (params: any) => {
        if (params.nodes.length > 0) {
          const node = allNodesRef.current.find((n) => n.id === params.nodes[0])
          setSelected(node ?? null)
        } else {
          setSelected(null)
        }
      })
    }

    if ((window as any).vis?.Network) {
      initNetwork()
    } else {
      // TODO(bundling): vis-network is not an app dependency, so it's pulled at
      // runtime from unpkg unpinned (latest) — a supply-chain and offline risk.
      // Once `npm i vis-network` + a bundled import are in, delete this whole
      // script-injection branch; keep the `window.vis` fast-path above.
      const script = document.createElement('script')
      script.src = 'https://unpkg.com/vis-network/standalone/umd/vis-network.min.js'
      script.onload = initNetwork
      script.onerror = () => setVisFailed(true)
      document.head.appendChild(script)
    }

    return () => {
      if (renderTimerRef.current) clearTimeout(renderTimerRef.current)
      networkRef.current?.destroy()
      networkRef.current = null
      nodesDataSetRef.current = null
      edgesDataSetRef.current = null
    }
  }, []) // empty deps — create network once

  // Keep allNodesRef in sync for click handler
  useEffect(() => {
    allNodesRef.current = nodes
  }, [nodes])

  // Incremental node addition — animate nodes appearing one by one.
  // Optimized: uses DataSet.add() instead of recreating the network.
  useEffect(() => {
    if (!nodesDataSetRef.current || !edgesDataSetRef.current || nodes.length === 0) return
    const tc = themeColorsRef.current
    if (!tc) return

    // Stop any in-flight batch from a previous run (e.g. a rebuild that fired
    // while this data was still streaming) so stale nodes can't land in the
    // freshly-cleared DataSet.
    if (renderTimerRef.current) {
      clearTimeout(renderTimerRef.current)
      renderTimerRef.current = null
    }

    // Clear previous data
    nodesDataSetRef.current.clear()
    edgesDataSetRef.current.clear()
    setRenderingProgress(0)

    // Build vis node objects
    const visNodes = nodes.map((n) => {
      const comm = n.community ?? n.cluster ?? 0
      const color = tc.palette[comm % tc.palette.length]
      const degree = n.degree_centrality ?? n.degree ?? 1
      return {
        id: n.id,
        label: n.label ?? n.id,
        title: `${n.type ?? 'node'}: ${n.file ?? ''}`,
        color: {
          background: color,
          border: color,
          highlight: { background: color, border: tc.textPrimary },
          hover: { background: color, border: tc.textPrimary },
        },
        size: 10 + Math.min(degree * 5, 40),
        font: { color: tc.textPrimary, size: 12, face: 'inherit', background: 'none' },
      }
    })

    const visEdges = links.map((e) => ({
      from: e.source,
      to: e.target,
      arrows: 'to',
      label: e.kind ?? e.relation ?? '',
      color: { color: tc.edgeColor, highlight: tc.edgeHighlight, hover: tc.accent },
      font: { color: tc.edgeFontColor, size: 10, face: 'inherit', background: 'none', strokeWidth: 0 },
    }))

    // Adaptive batching: target ~1.5s total animation
    const totalNodes = visNodes.length
    const batchCount = Math.min(50, Math.max(5, Math.ceil(totalNodes / 10)))
    const batchSize = Math.ceil(totalNodes / batchCount)
    const batchDelay = Math.max(20, Math.min(60, 1500 / batchCount))

    let nodeIndex = 0

    function addBatch() {
      if (!nodesDataSetRef.current) return

      const batch = visNodes.slice(nodeIndex, nodeIndex + batchSize)
      if (batch.length > 0) {
        nodesDataSetRef.current.add(batch)
      }
      nodeIndex += batchSize
      setRenderingProgress(Math.min(100, Math.round((nodeIndex / totalNodes) * 100)))

      if (nodeIndex < totalNodes) {
        renderTimerRef.current = setTimeout(addBatch, batchDelay)
      } else {
        // Add all edges after nodes are in place
        edgesDataSetRef.current.add(visEdges)
        setRenderingProgress(100)
        // Fit view with animation
        setTimeout(() => {
          networkRef.current?.fit({ animation: { duration: 500 } })
        }, 100)
      }
    }

    addBatch()
  }, [nodes, links])

  const handleBuild = () => {
    const payload: Record<string, string> = { mode: 'ast', backend }
    if (modelName) payload.model = modelName
    if (effectiveCwd) payload.cwd = effectiveCwd
    setStatus(prev => ({ ...prev, status: 'building', progress: 0, error: null }))
    buildGraph(payload).catch(() => {})
  }

  const handleCancel = () => {
    cancelBuild().catch(() => {})
    setStatus(prev => ({ ...prev, status: 'none', progress: 0 }))
  }

  const handleEnhance = () => {
    if (!modelName) return
    setStatus(prev => ({ ...prev, status: 'enhancing', progress: 0, error: null }))
    enhanceGraph(modelName).catch(() => {})
  }

  const statusTone: Record<string, 'good' | 'muted' | 'warn' | 'bad'> = {
    loading: 'muted',
    building: 'warn',
    enhancing: 'warn',
    ready: 'good',
    stale: 'warn',
    error: 'bad',
    none: 'muted',
    no_code: 'muted',
  }

  const badgeVariant: Record<string, 'default' | 'muted' | 'warn' | 'destructive' | 'outline'> = {
    loading: 'outline',
    building: 'warn',
    enhancing: 'warn',
    ready: 'default',
    stale: 'warn',
    error: 'destructive',
    none: 'outline',
    no_code: 'outline',
  }

  const isBuilding = status.status === 'building'
  const isEnhancing = status.status === 'enhancing'
  const isBusy = isBuilding || isEnhancing
  const isError = status.status === 'error'
  const hasNodes = nodes.length > 0
  const isReady = (status.status === 'ready' || status.graph_exists) && !isError
  const isEmpty = (status.status === 'none' || status.status === 'no_code') && !isBusy && !hasNodes

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <SelectStyles />
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-bold tracking-wide">Code Graph</h2>
          {effectiveCwd && (
            <div className="truncate text-xs" style={{ color: 'var(--ui-text-quaternary)', maxWidth: '400px' }}>
              {effectiveCwd}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Badge variant={badgeVariant[status.status] ?? 'outline'} size="default">
            <StatusDot tone={statusTone[status.status] ?? 'muted'} />
            {status.status}
          </Badge>
          <Button
            onClick={() => setSettingsOpen(!settingsOpen)}
            variant="ghost"
            size="icon-sm"
            title="Settings"
          >
            <Codicon name="settings-gear" />
          </Button>
          {isBusy ? (
            <Button
              onClick={handleCancel}
              variant="destructive"
              size="sm"
            >
              <Codicon name="stop" />
              Cancel
            </Button>
          ) : (
            <div className="flex items-center gap-1.5">
              <Button
                onClick={handleBuild}
                variant="secondary"
                size="sm"
              >
                <Codicon name="refresh" />
                Rebuild
              </Button>
              {isReady && modelsLive && !!modelName && (
                <Button
                  onClick={handleEnhance}
                  variant="ghost"
                  size="sm"
                  title="Enhance graph with local LLM (Ollama)"
                >
                  <Codicon name="sparkle" />
                  Enhance
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Build settings panel */}
      {settingsOpen && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg p-3 text-xs"
          style={{
            border: '1px solid var(--ui-stroke-secondary)',
            background: 'var(--ui-bg-editor)',
          }}
        >
          <div className="flex items-center gap-1">
            <span style={{ color: 'var(--ui-text-tertiary)' }}>Backend:</span>
            <select
              value={backend}
              onChange={(e) => { setBackend(e.target.value); setModelName('') }}
              className="graphify-select"
            >
              {['ollama', 'openai', 'claude', 'deepseek', 'gemini', 'kimi'].map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-1">
            <span style={{ color: 'var(--ui-text-tertiary)' }}>Model:</span>
            {modelList.length > 0 ? (
              <select
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="graphify-select"
                style={{ minWidth: '8rem' }}
              >
                <option value="">default</option>
                {modelList.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <Input
                placeholder="model name"
                value={modelName}
                onChange={(e: any) => setModelName(e.target.value)}
                className="w-32"
              />
            )}
          </div>
          {backend === 'ollama' && (
            <span style={{ color: modelsError ? 'var(--ui-red)' : 'var(--ui-text-quaternary)' }}>
              {modelsError
                ? modelsError
                : modelsLive
                  ? `${modelList.length} models available`
                  : 'Make sure Ollama is running'}
            </span>
          )}
        </div>
      )}

      {/* Status bar */}
      <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--ui-text-tertiary)' }}>
        <Badge variant="outline" size="xs">{status.node_count} nodes</Badge>
        <Badge variant="outline" size="xs">{status.edge_count} edges</Badge>
        <Badge variant="outline" size="xs">{status.community_count} communities</Badge>
        {status.backend && (
          <Badge variant="muted" size="xs">
            {status.backend}{status.model ? `/${status.model}` : ''}
          </Badge>
        )}
        {isBusy && (
          <div className="flex items-center gap-2">
            <div style={{
              width: '120px',
              height: '3px',
              background: 'var(--ui-stroke-secondary)',
              borderRadius: '2px',
              overflow: 'hidden',
            }}>
              <div style={{
                width: `${Math.round(status.progress)}%`,
                height: '100%',
                background: 'var(--ui-yellow)',
                borderRadius: '2px',
                transition: 'width 0.3s ease',
              }} />
            </div>
            <span style={{ color: 'var(--ui-yellow)' }}>
              {Math.round(status.progress)}%
            </span>
          </div>
        )}
        {hasNodes && renderingProgress > 0 && renderingProgress < 100 && (
          <div className="flex items-center gap-2">
            <div style={{
              width: '80px',
              height: '3px',
              background: 'var(--ui-stroke-secondary)',
              borderRadius: '2px',
              overflow: 'hidden',
            }}>
              <div style={{
                width: `${renderingProgress}%`,
                height: '100%',
                background: 'var(--ui-accent)',
                borderRadius: '2px',
                transition: 'width 0.1s ease',
              }} />
            </div>
            <span style={{ color: 'var(--ui-accent)' }}>rendering {renderingProgress}%</span>
          </div>
        )}
        {isError && (
          <span className="truncate" style={{ color: 'var(--ui-red)' }}>{status.error}</span>
        )}
      </div>

      {/* Main content */}
      <div className="flex min-h-0 flex-1 gap-3">
        {/* Wrapper: React manages overlays as siblings of the vis container */}
        <div
          className={cn("relative min-h-0 flex-1 rounded-lg")}
          style={{
            minHeight: '300px',
            border: '1px solid var(--ui-stroke-secondary)',
            background: 'var(--ui-bg-chrome)',
          }}
        >
          {/* vis-network target — NO React children here, vis owns this DOM */}
          <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

          {/* Overlays — siblings, not children of the vis container */}
          {(isBuilding || isEnhancing) && !hasNodes && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center" style={{ color: 'var(--ui-text-tertiary)' }}>
              <div className="text-center">
                <div className="mb-3 text-lg">{isEnhancing ? 'Enhancing graph...' : 'Building graph...'}</div>
                <div style={{
                  width: '200px',
                  height: '6px',
                  background: 'var(--ui-stroke-secondary)',
                  borderRadius: '3px',
                  overflow: 'hidden',
                  margin: '0 auto',
                }}>
                  <div style={{
                    width: `${Math.round(status.progress)}%`,
                    height: '100%',
                    background: 'var(--ui-yellow)',
                    borderRadius: '3px',
                    transition: 'width 0.3s ease',
                  }} />
                </div>
                <div className="mt-2 text-sm">{Math.round(status.progress)}% — {isEnhancing ? 'inferring deeper connections' : 'analyzing code dependencies'}</div>
              </div>
            </div>
          )}

          {/* Rebuild/enhance indicator — small badge if we already have a graph */}
          {isBusy && hasNodes && (
            <div className="pointer-events-none absolute right-2 top-2 flex items-center gap-2 rounded px-2 py-1 text-xs" style={{
              background: 'var(--ui-bg-editor)',
              border: '1px solid var(--ui-stroke-secondary)',
              color: 'var(--ui-yellow)',
            }}>
              <span>{isEnhancing ? 'Enhancing' : 'Rebuilding'}... {Math.round(status.progress)}%</span>
            </div>
          )}

          {/* Loading state — before first build, while fetching models/status */}
          {status.status === 'loading' && !isBusy && !hasNodes && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center" style={{ color: 'var(--ui-text-tertiary)' }}>
              <div className="text-center">
                <div className="mb-2 text-lg opacity-70">Loading...</div>
                <div className="text-sm opacity-50">Preparing code graph</div>
              </div>
            </div>
          )}

          {isEmpty && !isBusy && (
            <div className="pointer-events-auto absolute inset-0 flex items-center justify-center" style={{ color: 'var(--ui-text-tertiary)' }}>
              <div className="text-center">
                <div className="mb-2 text-lg">No graph yet</div>
                <div className="mb-3 text-sm">
                  {status.status === 'no_code'
                    ? 'No code files detected in working directory'
                    : 'Click build to analyze code dependencies'}
                </div>
                <Button onClick={handleBuild} size="sm">Build Graph</Button>
              </div>
            </div>
          )}

          {isError && (
            <div className="pointer-events-auto absolute inset-0 flex items-center justify-center" style={{ color: 'var(--ui-red)' }}>
              <div className="max-w-md text-center">
                <div className="mb-2 text-lg">Build Error</div>
                <div className="text-sm" style={{ color: 'var(--ui-text-tertiary)' }}>
                  {status.error ?? 'Unknown error'}
                </div>
                {status.warnings && (
                  <div className="mt-2 text-xs opacity-70" style={{ color: 'var(--ui-text-quaternary)' }}>
                    {status.warnings}
                  </div>
                )}
                {status.error?.includes('tensor') && (
                  <div className="mt-2 text-xs" style={{ color: 'var(--ui-yellow)' }}>
                    Model appears corrupted or incompatible. Try a different model in settings.
                  </div>
                )}
                {status.error?.includes('0 nodes') && (
                  <div className="mt-2 text-xs" style={{ color: 'var(--ui-yellow)' }}>
                    LLM backend failed. Check if Ollama is running and the model works.
                  </div>
                )}
                <div className="mt-3">
                  <Button onClick={handleBuild} size="sm">Retry</Button>
                </div>
              </div>
            </div>
          )}

          {visFailed && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center" style={{ color: 'var(--ui-text-tertiary)' }}>
              <div className="text-center">
                <div className="text-sm">Failed to load vis-network library</div>
                <div className="mt-1 text-xs opacity-60">Check internet connection</div>
              </div>
            </div>
          )}

          {hasNodes && !isBusy && (
            <div className="pointer-events-none absolute bottom-2 left-2 text-xs" style={{ color: 'var(--ui-text-quaternary)' }}>
              {nodes.length} nodes, {links.length} edges
            </div>
          )}
        </div>
        <div className="w-64 shrink-0 overflow-y-auto">
          {selected ? (
            <NodeInspector node={selected} links={links} onClose={() => setSelected(null)} />
          ) : (
            <GodNodesPanel nodes={nodes} links={links} />
          )}
          <ProjectSelector
            projects={projects}
            activeCwd={activeCwd}
            selectedProjectCwd={selectedProjectCwd}
            onSelect={(cwd) => {
              setSelectedProjectCwd(cwd)
              autoBuildTriggered.current = false
            }}
          />
        </div>
      </div>

    </div>
  )
}

// -- Node inspector ---------------------------------------------------------

function NodeInspector({ node, links, onClose }: {
  node: GraphNode
  links: GraphLink[]
  onClose: () => void
}) {
  const incoming = useMemo(() => links.filter((e) => e.target === node.id), [links, node.id])
  const outgoing = useMemo(() => links.filter((e) => e.source === node.id), [links, node.id])

  return (
    <div
      className="rounded-lg"
      style={{ border: '1px solid var(--ui-stroke-secondary)', background: 'var(--ui-bg-editor)' }}
    >
      <div
        className="flex items-center justify-between px-3 py-2"
        style={{ borderBottom: '1px solid var(--ui-stroke-secondary)' }}
      >
        <span className="text-sm font-semibold">{node.label ?? node.id}</span>
        <Button size="icon" onClick={onClose}>×</Button>
      </div>
      <div className="space-y-2 p-3 text-xs">
        <div><strong>Type: </strong>{node.type ?? 'unknown'}</div>
        {node.file && <div><strong>File: </strong>{node.file}</div>}
        {node.community != null && (
          <div><strong>Cluster: </strong>{node.community_label ?? `#${node.community}`}</div>
        )}
        {node.degree_centrality != null && (
          <div><strong>Degree: </strong>{node.degree_centrality.toFixed(3)}</div>
        )}
        {node.betweenness_centrality != null && (
          <div><strong>Betweenness: </strong>{node.betweenness_centrality.toFixed(3)}</div>
        )}
        <div className="pt-2"><strong>Incoming ({incoming.length})</strong></div>
        <div className="pl-2" style={{ color: 'var(--ui-text-tertiary)' }}>
          {incoming.map((e, i) => (
            <div key={i}>{e.source} ({e.kind ?? e.relation ?? ''})</div>
          ))}
        </div>
        <div className="pt-2"><strong>Outgoing ({outgoing.length})</strong></div>
        <div className="pl-2" style={{ color: 'var(--ui-text-tertiary)' }}>
          {outgoing.map((e, i) => (
            <div key={i}>{e.target} ({e.kind ?? e.relation ?? ''})</div>
          ))}
        </div>
      </div>
    </div>
  )
}

// -- God nodes panel --------------------------------------------------------

function GodNodesPanel({ nodes, links }: { nodes: GraphNode[]; links: GraphLink[] }) {
  const sorted = useMemo(() => {
    const degreeMap = new Map<string, number>()
    for (const l of links) {
      degreeMap.set(l.source, (degreeMap.get(l.source) ?? 0) + 1)
      degreeMap.set(l.target, (degreeMap.get(l.target) ?? 0) + 1)
    }
    return [...nodes]
      .sort((a, b) => (degreeMap.get(b.id) ?? 0) - (degreeMap.get(a.id) ?? 0))
      .slice(0, 10)
  }, [nodes, links])
  const degreeMap = useMemo(() => {
    const m = new Map<string, number>()
    for (const l of links) {
      m.set(l.source, (m.get(l.source) ?? 0) + 1)
      m.set(l.target, (m.get(l.target) ?? 0) + 1)
    }
    return m
  }, [links])

  return (
    <div
      className="rounded-lg"
      style={{ border: '1px solid var(--ui-stroke-secondary)', background: 'var(--ui-bg-editor)' }}
    >
      <div
        className="px-3 py-2"
        style={{ borderBottom: '1px solid var(--ui-stroke-secondary)' }}
      >
        <span className="text-sm font-semibold">Top Nodes</span>
      </div>
      <div className="space-y-1 p-3 text-xs">
        {sorted.map((n, i) => (
          <div
            key={n.id}
            className="flex cursor-pointer items-center justify-between py-0.5"
            style={{ transition: 'background 0.1s' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ui-row-hover-background)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <span className="truncate">{i + 1}. {n.label ?? n.id}</span>
            <span className="ml-2 shrink-0" style={{ color: 'var(--ui-text-tertiary)' }}>
              {degreeMap.get(n.id) ?? 0}
            </span>
          </div>
        ))}
        {sorted.length === 0 && (
          <div style={{ color: 'var(--ui-text-tertiary)' }}>No nodes yet</div>
        )}
      </div>
    </div>
  )
}

// -- Project selector --------------------------------------------------------

interface ProjectSelectorProps {
  projects: { id: string; name: string; primary_path: null | string; folders: { path: string }[]; archived?: boolean }[]
  activeCwd: string
  selectedProjectCwd: null | string
  onSelect: (cwd: string) => void
}

function ProjectSelector({ projects, activeCwd, selectedProjectCwd, onSelect }: ProjectSelectorProps) {
  const activeCwdTrim = activeCwd.trim().toLowerCase()

  const entries = useMemo(() => {
    return projects
      .filter(p => !p.archived)
      .map(p => {
        const cwd = (p.primary_path ?? p.folders[0]?.path ?? '').trim()
        const isActive = cwd.toLowerCase() === activeCwdTrim
        const isSelected = cwd === selectedProjectCwd
        return { id: p.id, name: p.name || p.id, cwd, isActive, isSelected }
      })
      .filter(e => e.cwd)
  }, [projects, activeCwdTrim, selectedProjectCwd])

  if (entries.length === 0) return null

  return (
    <div
    className="mt-2 rounded-lg"
    style={{ border: '1px solid var(--ui-stroke-secondary)', background: 'var(--ui-bg-editor)' }}
  >
      <div
        className="px-3 py-2"
        style={{ borderBottom: '1px solid var(--ui-stroke-secondary)' }}
      >
        <span className="text-sm font-semibold">Projects</span>
      </div>
      <div className="space-y-1 p-2 text-xs">
        {entries.map(e => (
          <div
            key={e.id}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1"
            style={{
              background: e.isSelected
                ? 'var(--ui-accent-soft, color-mix(in srgb, var(--ui-accent) 12%, transparent))'
                : e.isActive
                  ? 'var(--ui-row-hover-background)'
                  : 'transparent',
              transition: 'background 0.1s',
            }}
            onMouseEnter={(ev) => {
              if (!e.isSelected && !e.isActive) ev.currentTarget.style.background = 'var(--ui-row-hover-background)'
            }}
            onMouseLeave={(ev) => {
              if (!e.isSelected && !e.isActive) ev.currentTarget.style.background = 'transparent'
            }}
            onClick={() => onSelect(e.cwd)}
          >
            <span className="truncate" style={{ color: e.isSelected ? 'var(--ui-accent)' : 'var(--ui-text-primary)' }}>
              {e.name}
            </span>
            {e.isActive && !e.isSelected && (
              <span className="ml-auto shrink-0" style={{ color: 'var(--ui-text-quaternary)' }}>chat</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Select styles ───────────────────────────────────────────────────────────
//
// Native <select> elements can't be fully styled with inline styles because
// the dropdown list (option popup) is OS-rendered. We use a CSS class + <style>
// tag injected once to style both the trigger and the option list as closely
// as the browser allows, matching the Hermes design system tokens.

const _selectStyles = `
.graphify-select {
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  border: 1px solid var(--ui-stroke-secondary);
  background: var(--ui-bg-input);
  color: var(--ui-text-primary);
  font-size: 0.75rem;
  line-height: 1.25rem;
  padding: 0.125rem 1.5rem 0.125rem 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12' fill='none'%3E%3Cpath d='M3 4.5L6 7.5L9 4.5' stroke='%23888' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.25rem center;
  background-size: 12px;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}
.graphify-select:hover {
  border-color: var(--ui-stroke-primary, var(--ui-accent));
}
.graphify-select:focus {
  outline: none;
  border-color: var(--ui-accent);
  box-shadow: 0 0 0 1px var(--ui-accent);
}
.graphify-select option {
  background: var(--ui-bg-editor, #1e1e24);
  color: var(--ui-text-primary, #e0e0e0);
  padding: 0.25rem 0.5rem;
}
`

function SelectStyles() {
  return <style dangerouslySetInnerHTML={{ __html: _selectStyles }} />
}
