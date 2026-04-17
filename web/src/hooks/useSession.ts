import { useState, useEffect, useCallback } from 'react'
import type { Session } from '../types'
import { listSessions, createSession, deleteSession, restoreSession, listDeletedSessions } from '../api/sessions'

export function useSession() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [deletedSessions, setDeletedSessions] = useState<Session[]>([])
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

  const refreshDeleted = useCallback(async () => {
    try {
      const list = await listDeletedSessions()
      setDeletedSessions(list.sort((a, b) => (b.time.updated ?? b.time.created) - a.time.created))
    } catch (err) {
      console.error('Failed to load deleted sessions', err)
    }
  }, [])

  useEffect(() => {
    refresh()
    refreshDeleted()
  }, [refresh, refreshDeleted])

  const create = useCallback(async () => {
    const s = await createSession()
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    return s
  }, [])

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id)
      } catch (err) {
        console.error('Failed to delete session', err)
      }
      // Move from active list to deleted list
      setSessions((prev) => {
        const target = prev.find((s) => s.id === id)
        if (target) {
          setDeletedSessions((del) => [{ ...target, visible: false }, ...del])
        }
        return prev.filter((s) => s.id !== id)
      })
      if (activeId === id) setActiveId(null)
    },
    [activeId],
  )

  const restore = useCallback(
    async (id: string) => {
      try {
        await restoreSession(id)
      } catch (err) {
        console.error('Failed to restore session', err)
        return
      }
      // Move from deleted list back to active list
      setDeletedSessions((prev) => {
        const target = prev.find((s) => s.id === id)
        if (target) {
          setSessions((cur) => [{ ...target, visible: true }, ...cur])
        }
        return prev.filter((s) => s.id !== id)
      })
    },
    [],
  )

  const active = sessions.find((s) => s.id === activeId) ?? null

  return { sessions, deletedSessions, active, activeId, setActiveId, create, remove, restore, loading, refresh }
}
