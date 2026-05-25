// FILE: src/main.ts
// PURPOSE: Kanban board with Alpine.js — WebSocket, drag-drop, modal, filters.
// NOTE: Using a single Alpine component on #app for guaranteed reactivity.

import Alpine from 'alpinejs'
import Sortable from 'sortablejs'
import type { Overview, ServerMessage, Task } from './types'

/* eslint-disable @typescript-eslint/no-explicit-any */

Alpine.data('kanban', () => ({
  // === Reactive state (Alpine-managed, guaranteed reactive) ===
  overview: null as Overview | null,
  connected: false,

  // WS (never reactive, stored on component)
  _ws: null as WebSocket | null,
  _reconnectTimer: null as number | null,
  _connectTimer: null as number | null,
  _reconnectAttempts: 0,

  // Modal
  modalOpen: false,
  modalMode: 'edit' as 'create' | 'edit',
  modalTask: null as Task | null,
  modalEditTitle: '',
  modalEditStatus: '',
  modalEditPriority: 2,
  modalEditClaimed: '',
  modalEditTags: '',
  modalEditNotes: '',

  // Filters
  searchQuery: '',
  filterTags: [] as string[],
  filterClaimed: '',
  filterPriority: null as number | null,

  // Column collapse
  collapsedColumns: {} as Record<string, boolean>,

  // Sortable instances
  _sortables: [] as Sortable[],

  // === Lifecycle ===
  init() {
    console.log('[Kanban] Component initializing')
    this.connect()
  },

  destroy() {
    this._destroySortables()
    this._cleanupWS()
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
    if (this._connectTimer !== null) {
      clearTimeout(this._connectTimer)
      this._connectTimer = null
    }
  },

  _cleanupWS() {
    if (this._ws) {
      this._ws.onopen = null
      this._ws.onclose = null
      this._ws.onerror = null
      this._ws.onmessage = null
      if (this._ws.readyState === WebSocket.OPEN ||
          this._ws.readyState === WebSocket.CONNECTING) {
        this._ws.close()
      }
      this._ws = null
    }
  },

  // === WebSocket ===
  connect() {
    // Close any existing connection
    this._cleanupWS()
    if (this._connectTimer !== null) {
      clearTimeout(this._connectTimer)
      this._connectTimer = null
    }

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws`
    console.log('[WS] Connecting to', url)
    const ws = new WebSocket(url)
    this._ws = ws

    // Connection timeout: if onopen doesn't fire in 3s, abort and retry
    this._connectTimer = window.setTimeout(() => {
      if (!this._ws || this._ws !== ws) return // stale timer
      if (ws.readyState === WebSocket.CONNECTING) {
        console.log('[WS] Connection timeout — closing')
        ws.onclose = null // prevent firing onclose
        ws.close()
        this._connectTimer = null
        this._scheduleReconnect()
      }
    }, 3000)

    ws.onopen = () => {
      console.log('[WS] Connected')
      if (this._connectTimer !== null) {
        clearTimeout(this._connectTimer)
        this._connectTimer = null
      }
      if (this._reconnectTimer !== null) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
      this._reconnectAttempts = 0
      this.connected = true
    }

    ws.onclose = (event) => {
      console.log('[WS] Closed code=' + event.code + ' reason=' + event.reason)
      if (this._connectTimer !== null) {
        clearTimeout(this._connectTimer)
        this._connectTimer = null
      }
      this.connected = false
      this._scheduleReconnect()
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: ServerMessage = JSON.parse(event.data)
        this._handleMessage(msg)
      } catch (e) {
        console.error('[WS] Message error:', e)
      }
    }

    ws.onerror = () => {
      console.log('[WS] Error event (onclose will follow)')
    }
  },

  _scheduleReconnect() {
    if (this._reconnectTimer !== null) return
    // Exponential backoff: 500ms, 1s, 2s, 4s, capped at 10s
    this._reconnectAttempts++
    const delay = Math.min(500 * Math.pow(2, this._reconnectAttempts - 1), 10000)
    console.log('[WS] Reconnect in ' + delay + 'ms (attempt #' + this._reconnectAttempts + ')')
    this._reconnectTimer = window.setTimeout(() => {
      this._reconnectTimer = null
      this.connect()
    }, delay)
  },

  _handleMessage(msg: ServerMessage) {
    switch (msg.type) {
      case 'overview': {
        const counts = msg.data?.counts || {}
        console.log('[WS] Overview received — ' +
          'active: ' + (counts.active ?? '?') +
          ', done: ' + (counts.done ?? '?') +
          ', statuses: ' + (msg.data?.statuses?.length ?? '?'))
        this.overview = msg.data
        setTimeout(() => this._initSortables(), 50)
        break
      }
      case 'task_created':
      case 'task_updated':
        console.log('[WS] ' + msg.type + ' — requesting refresh')
        this._send({ type: 'request_overview' })
        break
      case 'task_deleted':
        console.log('[WS] task_deleted — requesting refresh')
        this._send({ type: 'request_overview' })
        break
      case 'pong':
        break
      case 'error':
        console.error('[WS] Server error:', msg.message)
        break
    }
  },

  _send(msg: object) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(msg))
    }
  },

  // === Board actions ===
  openCreateModal(status: string) {
    this.modalMode = 'create'
    this.modalTask = null
    this.modalEditTitle = ''
    this.modalEditStatus = status
    this.modalEditPriority = 2
    this.modalEditClaimed = ''
    this.modalEditTags = ''
    this.modalEditNotes = ''
    this.modalOpen = true

    // Auto-focus the title input after modal renders
    this.$nextTick(() => {
      const input = this.$el.querySelector('.modal-title-input') as HTMLInputElement | null
      input?.focus()
    })
  },

  closeTask(id: string) {
    this._send({ type: 'close_task', id })
    if (this.modalOpen && this.modalTask?.id === id) {
      this.closeModal()
    }
  },

  deleteTask(id: string) {
    if (confirm('Delete this task permanently?')) {
      this._send({ type: 'delete_task', id })
      if (this.modalOpen && this.modalTask?.id === id) {
        this.closeModal()
      }
    }
  },

  moveTask(id: string, newStatus: string, newOrder: number) {
    this._send({ type: 'update_task', id, status: newStatus, order: newOrder })
  },

  // === Modal ===
  openModal(task: Task) {
    this.modalMode = 'edit'
    this.modalTask = task
    this.modalEditTitle = task.title
    this.modalEditStatus = task.status
    this.modalEditPriority = task.priority ?? 2
    this.modalEditClaimed = task.claimed || ''
    this.modalEditTags = (task.tags || []).join(', ')
    this.modalEditNotes = (task.notes || []).join('\n')
    this.modalOpen = true
  },

  closeModal() {
    this.modalOpen = false
    this.modalTask = null
    this.modalMode = 'edit'
  },

  saveModal() {
    if (this.modalMode === 'create') {
      const title = this.modalEditTitle.trim()
      if (!title) return
      const status = this.modalEditStatus
      const tasks = this.tasksByStatus(status)
      const maxOrder = tasks.reduce((max, t) => Math.max(max, t.order || 0), 0)
      this._send({
        type: 'add_task',
        title,
        status,
        priority: this.modalEditPriority,
        claimed: this.modalEditClaimed || null,
        tags: this.modalEditTags.split(',').map(t => t.trim()).filter(t => t.length > 0),
        notes: this.modalEditNotes,
        source: 'web',
        order: maxOrder + 1000,
      })
    } else {
      if (!this.modalTask) return
      const msg: Record<string, any> = {
        type: 'update_task',
        id: this.modalTask.id,
        title: this.modalEditTitle,
        status: this.modalEditStatus,
        priority: this.modalEditPriority,
        claimed: this.modalEditClaimed || null,
        tags: this.modalEditTags.split(',').map(t => t.trim()).filter(t => t.length > 0),
        notes: this.modalEditNotes,
        replace_tags: true,
        replace_notes: true,
      }
      this._send(msg)
    }
    this.closeModal()
  },

  // === Filters ===
  toggleFilterTag(tag: string) {
    const idx = this.filterTags.indexOf(tag)
    if (idx >= 0) {
      this.filterTags.splice(idx, 1)
    } else {
      this.filterTags.push(tag)
    }
    // Trigger reactivity on array
    this.filterTags = [...this.filterTags]
  },

  setFilterClaimed(val: string) {
    this.filterClaimed = val === this.filterClaimed ? '' : val
  },

  setFilterPriority(val: number | null) {
    this.filterPriority = this.filterPriority === val ? null : val
  },

  clearFilters() {
    this.searchQuery = ''
    this.filterTags = []
    this.filterClaimed = ''
    this.filterPriority = null
  },

  // === Column ===
  closeAllInColumn(status: string) {
    const tasks = this.tasksByStatus(status)
    const count = tasks.filter(t => !t.closed).length
    if (count === 0) return
    if (!confirm(`Close all ${count} cards in "${status}"?`)) return
    for (const t of tasks) {
      if (!t.closed) this.closeTask(t.id)
    }
  },

  toggleCollapse(status: string) {
    this.collapsedColumns = {
      ...this.collapsedColumns,
      [status]: !this.collapsedColumns[status],
    }
  },

  isCollapsed(status: string): boolean {
    return !!this.collapsedColumns[status]
  },

  // === Computed helpers ===
  tasksByStatus(status: string): Task[] {
    if (!this.overview) return []

    let allTasks: Task[] = []
    const sections = ['now', 'ready', 'waiting', 'parked'] as const
    for (const section of sections) {
      allTasks = allTasks.concat(this.overview[section])
    }
    if (this.overview.done) {
      allTasks = allTasks.concat(this.overview.done)
    }

    let tasks = allTasks.filter(t => t.status === status)

    if (this.searchQuery) {
      const q = this.searchQuery.toLowerCase()
      tasks = tasks.filter(t =>
        t.title.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        (t.tags || []).some(tag => tag.toLowerCase().includes(q))
      )
    }
    if (this.filterTags.length > 0) {
      tasks = tasks.filter(t =>
        this.filterTags.some(tag => (t.tags || []).includes(tag))
      )
    }
    if (this.filterClaimed) {
      tasks = tasks.filter(t => t.claimed === this.filterClaimed)
    }
    if (this.filterPriority !== null) {
      tasks = tasks.filter(t => t.priority === this.filterPriority)
    }

    tasks.sort((a, b) => {
      if (a.closed !== b.closed) return a.closed ? 1 : -1
      const aOrder = a.order ?? 0
      const bOrder = b.order ?? 0
      if (aOrder !== bOrder) return aOrder - bOrder
      return (a.created || '').localeCompare(b.created || '')
    })

    return tasks
  },

  allTags(): string[] {
    if (!this.overview) return []
    const tags = new Set<string>()
    const sections = ['now', 'ready', 'waiting', 'parked'] as const
    for (const section of sections) {
      for (const t of this.overview[section]) {
        for (const tag of t.tags || []) tags.add(tag)
      }
    }
    return Array.from(tags).sort()
  },

  allClaimed(): string[] {
    if (!this.overview) return []
    const claimed = new Set<string>()
    const sections = ['now', 'ready', 'waiting', 'parked'] as const
    for (const section of sections) {
      for (const t of this.overview[section]) {
        if (t.claimed) claimed.add(t.claimed)
      }
    }
    return Array.from(claimed).sort()
  },

  filteredCount(): number {
    if (!this.overview) return 0
    const statuses = this.overview.statuses || []
    return statuses.reduce((sum, s) => sum + this.tasksByStatus(s).length, 0)
  },

  // === SortableJS ===
  _initSortables() {
    this._destroySortables()
    setTimeout(() => {
      const lists = document.querySelectorAll('.card-list')
      lists.forEach((el) => {
        if (!el || !(el instanceof HTMLElement)) return
        const column = el.closest('.column') as HTMLElement | null
        if (!column) return
        const status = column.dataset.status
        if (!status) return

        const sortable = Sortable.create(el, {
          group: 'kanban',
          animation: 150,
          ghostClass: 'card-ghost',
          dragClass: 'card-dragging',
          easing: 'cubic-bezier(0.25, 0.1, 0.25, 1.0)',
          onEnd: (evt) => {
            const taskId = evt.item.dataset.taskId
            if (!taskId) return
            const targetColumn = evt.to.closest('.column') as HTMLElement | null
            const newStatus = targetColumn?.dataset.status
            if (!newStatus) return

            const newIndex = evt.newIndex ?? 0
            let newOrder = (newIndex + 1) * 1000

            if (newStatus === status) {
              const tasks = this.tasksByStatus(status)
              if (tasks.length > 1) {
                if (newIndex === 0) {
                  newOrder = (tasks[0].order ?? 1000) - 1000
                } else if (newIndex >= tasks.length - 1) {
                  newOrder = (tasks[tasks.length - 1].order ?? 0) + 1000
                } else {
                  const before = tasks[newIndex - 1]?.order ?? 0
                  const after = tasks[newIndex]?.order ?? 100000
                  newOrder = Math.floor((before + after) / 2)
                }
              }
            } else {
              const targetTasks = this.tasksByStatus(newStatus)
              if (newIndex <= 0 || targetTasks.length === 0) {
                newOrder = 1000
              } else {
                newOrder = (newIndex) * 1000
              }
            }
            this.moveTask(taskId, newStatus, newOrder)
          },
        })
        this._sortables.push(sortable)
      })
    }, 50)
  },

  _destroySortables() {
    this._sortables.forEach(s => s.destroy())
    this._sortables = []
  },
}))

Alpine.start()
