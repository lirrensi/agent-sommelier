// FILE: src/agent_sommelier/web/src/types.ts
// PURPOSE: TypeScript interfaces for task data and WebSocket protocol messages.
// OWNS: Shared type definitions used by main.ts and any future web components.
// EXPORTS: Task, OverviewSections, Overview, Meta, ServerMessage
// DOCS: .agents/reports/plan_web_ui_2026-05-24.md

export interface Task {
  id: string
  title: string
  status: string
  priority: number
  tags?: string[]
  notes?: string[]
  evidence?: string[]
  deps?: { id: string; type: string }[]
  claimed?: string | null
  closed: boolean
  created: string
  updated?: string
  hint?: string
}

export interface OverviewSections {
  now: Task[]
  ready: Task[]
  waiting: Task[]
  parked: Task[]
}

export interface Overview {
  now: Task[]
  ready: Task[]
  waiting: Task[]
  parked: Task[]
  counts: {
    active: number
    now: number
    ready: number
    waiting: number
    parked: number
  }
}

export interface Meta {
  counter: number
  config: Record<string, unknown>
}

export type ServerMessage =
  | { type: 'overview'; data: Overview }
  | { type: 'meta'; data: Meta }
  | { type: 'task_created'; task: Task }
  | { type: 'task_updated'; task: Task }
  | { type: 'task_deleted'; task_id: string }
  | { type: 'pong' }
  | { type: 'error'; message: string }
