// FILE: src/types.ts
// PURPOSE: TypeScript interfaces for task data and WebSocket protocol messages.

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
  created?: string
  updated?: string
  closed_at?: string
  createdBy?: string
  source?: string
  order?: number
  hint?: string
}

export interface Overview {
  now: Task[]
  ready: Task[]
  waiting: Task[]
  parked: Task[]
  done: Task[]
  counts: Record<string, number>
  statuses: string[]
}

export type ServerMessage =
  | { type: 'overview'; data: Overview }
  | { type: 'task_created'; task: Task }
  | { type: 'task_updated'; task: Task }
  | { type: 'task_deleted'; task_id: string }
  | { type: 'pong' }
  | { type: 'error'; message: string }
