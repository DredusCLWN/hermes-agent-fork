/**
 * Graphify — code dependency graph plugin for the Hermes desktop app.
 *
 * Registers a `/graph` route + sidebar nav row + palette command.
 * The page renders an interactive vis-network graph of the codebase.
 *
 * Ships ON by default (`defaultEnabled: true`): the graph auto-builds
 * when code files are detected in the working directory.
 */

import {
  cn,
  Codicon,
  type HermesPlugin,
  host,
  type PaletteContribution,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
} from '@hermes/plugin-sdk'

import { GraphCanvas } from './graph-canvas'
import { bindApi } from './api'

const plugin: HermesPlugin = {
  id: 'graphify',
  name: 'Code Graph',
  defaultEnabled: true,
  register(ctx) {
    ctx.onDispose(bindApi(ctx.rest))
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/graph' } satisfies RouteContribution,
        render: () => <GraphCanvas />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 45,
        data: { codicon: 'code', label: 'Code Graph', path: '/graph' } satisfies SidebarNavContribution
      },
      {
        id: 'open',
        area: 'palette' as any,
        data: {
          id: 'graphify.open',
          label: 'Code Graph: Open graph',
          keywords: ['graph', 'code', 'dependencies', 'graphify', 'visualize'],
          run: () => host.navigate('/graph')
        } satisfies PaletteContribution
      },
    ])
  }
}

export default plugin
