import { useState, useEffect, useCallback } from 'react'
import type { Session } from '../types'
import { listSessions, createSession } from '../api/sessions'

export function useSession() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(list.sort((a, b) => b.time.created - a.time.created))
    } catch (err) {
      console.error('Failed to load sessions', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const create = useCallback(async () => {
    const s = await createSession()
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    return s
  }, [])

  const remove = useCallback(
    (id: string) => {
      // Only remove from UI display and clear active state; do NOT delete from database
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (activeId === id) setActiveId(null)
    },
    [activeId],
  )

  const active = sessions.find((s) => s.id === activeId) ?? null

  return { sessions, active, activeId, setActiveId, create, remove, loading, refresh }
}
