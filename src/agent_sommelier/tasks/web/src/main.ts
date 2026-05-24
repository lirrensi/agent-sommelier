// FILE: src/agent_sommelier/web/src/main.ts
// PURPOSE: Alpine.js entry point — connect WebSocket, manage reactive task state.
// OWNS: WebSocket lifecycle, message dispatch, and Alpine store registration.
// EXPORTS: (none — side-effect module that calls Alpine.start())
// DOCS: .agents/reports/plan_web_ui_2026-05-24.md

import Alpine from 'alpinejs'
import type { Overview, ServerMessage } from './types'

/* eslint-disable @typescript-eslint/no-explicit-any */

interface TaskStore {
  overview: Overview | null
  connected: boolean
  ws: WebSocket | null
  reconnectTimer: number | null
  connect(): void
  scheduleReconnect(): void
  handleMessage(msg: ServerMessage): void
  send(msg: object): void
  addTask(title: string, priority: number, tags: string): void
  takeTask(id: string): void
  closeTask(id: string): void
}

const storeObject: TaskStore = {
  // --- Reactive state ---
  overview: null,
  connected: false,
  ws: null,
  reconnectTimer: null,

  // --- WebSocket lifecycle ---
  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws`
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      this.connected = true
      if (this.reconnectTimer !== null) {
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
      }
    }

    ws.onclose = () => {
      this.connected = false
      this.scheduleReconnect()
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: ServerMessage = JSON.parse(event.data)
        this.handleMessage(msg)
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e)
      }
    }

    ws.onerror = () => {
      // onclose will fire after onerror, so reconnect is handled there
    }
  },

  scheduleReconnect() {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 2000)
  },

  // --- Message handling ---
  handleMessage(msg: ServerMessage) {
    switch (msg.type) {
      case 'overview':
        this.overview = msg.data
        break
      case 'task_created':
      case 'task_updated':
      case 'task_deleted':
        // Overview is authoritative; no per-task state needed
        break
      case 'pong':
        break
      case 'error':
        console.error('Server error:', msg.message)
        break
    }
  },

  // --- Send helpers ---
  send(msg: object) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  },

  addTask(title: string, priority: number, tags: string) {
    const payload: Record<string, unknown> = { type: 'add_task', title, priority }
    if (tags && tags.trim()) {
      payload.tags = tags.split(',').map(t => t.trim()).filter(t => t.length > 0)
    }
    this.send(payload)
  },

  takeTask(id: string) {
    this.send({ type: 'take_task', id })
  },

  closeTask(id: string) {
    this.send({ type: 'close_task', id })
  },
}

document.addEventListener('alpine:init', () => {
  Alpine.store('tasks', storeObject as any)
})

Alpine.start()

// Auto-connect when the page loads
if (typeof window !== 'undefined') {
  window.addEventListener('load', () => {
    // The store is registered by now; use a small delay to let Alpine initialize
    setTimeout(() => {
      const store = Alpine.store('tasks') as unknown as TaskStore
      if (store && typeof store.connect === 'function') {
        store.connect()
      }
    }, 100)
  })
}
