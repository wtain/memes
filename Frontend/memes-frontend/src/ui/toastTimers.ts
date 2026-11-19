type Entry = { timeout: ReturnType<typeof setTimeout> | null; remaining: number; startedAt: number }

/** Pausable per-toast dismiss timers. Insertion order is age order. */
export class ToastTimers {
  private entries = new Map<number, Entry>()
  private lastId = 0

  nextId(): number {
    return ++this.lastId
  }

  get size(): number {
    return this.entries.size
  }

  add(id: number, ms: number, onExpire: () => void): void {
    this.entries.set(id, { timeout: null, remaining: ms, startedAt: 0 })
    this.arm(id, ms, onExpire)
  }

  private arm(id: number, ms: number, onExpire: () => void): void {
    const entry = this.entries.get(id)
    if (!entry) return
    entry.remaining = ms
    entry.startedAt = Date.now()
    entry.timeout = setTimeout(onExpire, ms)
  }

  pause(id: number): void {
    const entry = this.entries.get(id)
    if (!entry || entry.timeout === null) return
    clearTimeout(entry.timeout)
    entry.timeout = null
    entry.remaining = Math.max(0, entry.remaining - (Date.now() - entry.startedAt))
  }

  resume(id: number, onExpire: () => void): void {
    const entry = this.entries.get(id)
    if (!entry || entry.timeout !== null) return
    this.arm(id, entry.remaining, onExpire)
  }

  delete(id: number): void {
    const entry = this.entries.get(id)
    if (entry?.timeout) clearTimeout(entry.timeout)
    this.entries.delete(id)
  }

  oldest(): number | undefined {
    return this.entries.keys().next().value
  }

  clear(): void {
    for (const entry of this.entries.values()) if (entry.timeout) clearTimeout(entry.timeout)
    this.entries.clear()
  }
}
