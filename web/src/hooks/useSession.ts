import { useState, useEffect, useCallback, useMemo } from 'react'
import type { Session } from '../types'
import { listSessions, createSession, deleteSession, restoreSession, listDeletedSessions } from '../api/sessions'

const ACTIVE_SESSION_STORAGE_KEY = 'mycode.activeSessionId'

function readStoredActiveSession(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY)
}

function persistActiveSession(id: string | null) {
  if (typeof window === 'undefined') return
  if (id) {
    window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, id)
  } else {
    window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY)
  }
}

export function useSession() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [deletedSessions, setDeletedSessions] = useState<Session[]>([])
  const [activeId, setActiveIdState] = useState<string | null>(() => readStoredActiveSession())
  const [lastActiveId, setLastActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(
        list.sort((a, b) => (b.time.updated ?? b.time.created) - (a.time.updated ?? a.time.created)),
      )
    } catch (err) {
      console.error('Failed to load sessions', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshDeleted = useCallback(async () => {
    try {
      const list = await listDeletedSessions()
      setDeletedSessions(list.sort((a, b) => (b.time.updated ?? b.time.created) - (a.time.updated ?? a.time.created)))
    } catch (err) {
      console.error('Failed to load deleted sessions', err)
    }
  }, [])

  useEffect(() => {
    refresh()
    refreshDeleted()
  }, [refresh, refreshDeleted])

  useEffect(() => {
    persistActiveSession(activeId)
  }, [activeId])

  useEffect(() => {
    if (!activeId) return
    if (sessions.some((session) => session.id === activeId)) return
    setActiveIdState(null)
  }, [activeId, sessions])

  const setActiveId = useCallback((id: string | null) => {
    setActiveIdState((prev) => {
      if (prev && prev !== id) {
        setLastActiveId(prev)
      }
      return id
    })
  }, [])

  const create = useCallback(async () => {
    const s = await createSession()
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    return s
  }, [setActiveId])

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id)
      } catch (err) {
        console.error('Failed to delete session', err)
      }
      setSessions((prev) => {
        const target = prev.find((s) => s.id === id)
        if (target) {
          setDeletedSessions((del) => [{ ...target, visible: false }, ...del])
        }
        return prev.filter((s) => s.id !== id)
      })
      if (activeId === id) {
        setActiveIdState(null)
      }
    },
    [activeId],
  )

  const restore = useCallback(async (id: string) => {
    try {
      await restoreSession(id)
    } catch (err) {
      console.error('Failed to restore session', err)
      return
    }

    setDeletedSessions((prev) => {
      const target = prev.find((s) => s.id === id)
      if (target) {
        setSessions((cur) => [{ ...target, visible: true }, ...cur])
      }
      return prev.filter((s) => s.id !== id)
    })

    setActiveId(id)
  }, [setActiveId])

  const returnToLastSession = useCallback(() => {
    if (!lastActiveId) return

    setActiveIdState((current) => {
      if (current && current !== lastActiveId) {
        setLastActiveId(current)
      }
      return lastActiveId
    })
  }, [lastActiveId])

  const active = sessions.find((s) => s.id === activeId) ?? null
  const canReturnToLastSession = useMemo(
    () => Boolean(lastActiveId && lastActiveId !== activeId && sessions.some((session) => session.id === lastActiveId)),
    [activeId, lastActiveId, sessions],
  )

  return {
    sessions,
    deletedSessions,
    active,
    activeId,
    setActiveId,
    create,
    remove,
    restore,
    loading,
    refresh,
    lastActiveId,
    returnToLastSession,
    canReturnToLastSession,
  }
}
