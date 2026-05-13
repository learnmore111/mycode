import { useState, useEffect, useCallback, useMemo } from 'react'
import type { Session, WorkspaceProject } from '../types'
import { listSessions, createSession, deleteSession, restoreSession, listDeletedSessions } from '../api/sessions'

export interface SessionProject extends WorkspaceProject {
  sessions: Session[]
  deletedSessions: Session[]
  loading: boolean
}

const OPEN_PROJECTS_STORAGE_KEY = 'mycode.openProjects.v1'
const ACTIVE_PROJECT_STORAGE_KEY = 'mycode.activeProjectDir.v1'
const ACTIVE_SESSIONS_STORAGE_KEY = 'mycode.activeSessionsByProject.v1'

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) as T : fallback
  } catch {
    return fallback
  }
}

function persistJson(key: string, value: unknown) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(key, JSON.stringify(value))
}

function readStoredProjectDirectories(): string[] {
  if (typeof window === 'undefined') return ['.']
  try {
    const raw = window.localStorage.getItem(OPEN_PROJECTS_STORAGE_KEY)
    if (raw == null) return ['.']
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : ['.']
  } catch {
    return ['.']
  }
}

function sortSessions(list: Session[]): Session[] {
  return [...list].sort((a, b) => (b.time.updated ?? b.time.created) - (a.time.updated ?? a.time.created))
}

function normalizeDirectory(directory: string): string {
  return directory.replace(/[\\/]+$/, '')
}

function basename(directory: string): string {
  const trimmed = normalizeDirectory(directory)
  const parts = trimmed.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] || trimmed || directory
}

function buildWorkspaceProject(directory: string): WorkspaceProject {
  return {
    id: directory,
    directory,
    name: basename(directory),
    worktree: directory,
  }
}

async function loadProjectState(directory: string): Promise<SessionProject | null> {
  try {
    const [sessions, deletedSessions] = await Promise.all([
      listSessions(directory),
      listDeletedSessions(directory),
    ])
    const normalizedDirectory = normalizeDirectory(directory)
    const scopedSessions = sessions.filter((session) => normalizeDirectory(session.directory) === normalizedDirectory)
    const scopedDeletedSessions = deletedSessions.filter((session) => normalizeDirectory(session.directory) === normalizedDirectory)

    return {
      ...buildWorkspaceProject(directory),
      sessions: sortSessions(scopedSessions),
      deletedSessions: sortSessions(scopedDeletedSessions),
      loading: false,
    }
  } catch (err) {
    console.error('Failed to load project state', directory, err)
    return null
  }
}

function createPendingProject(directory: string): SessionProject {
  return {
    ...buildWorkspaceProject(directory),
    sessions: [],
    deletedSessions: [],
    loading: true,
  }
}

export function useSession() {
  const [projects, setProjects] = useState<SessionProject[]>([])
  const [activeProjectDirectory, setActiveProjectDirectoryState] = useState<string | null>(() =>
    readJson<string | null>(ACTIVE_PROJECT_STORAGE_KEY, null),
  )
  const [activeSessionsByProject, setActiveSessionsByProject] = useState<Record<string, string | null>>(() =>
    readJson<Record<string, string | null>>(ACTIVE_SESSIONS_STORAGE_KEY, {}),
  )
  const [lastActive, setLastActive] = useState<{ directory: string; sessionId: string } | null>(null)
  const [loading, setLoading] = useState(true)

  const setActiveProjectDirectory = useCallback((directory: string | null) => {
    setActiveProjectDirectoryState(directory)
  }, [])

  useEffect(() => {
    let cancelled = false

    const hydrate = async () => {
      setLoading(true)
      const seeds = readStoredProjectDirectories()
      const loaded = (await Promise.all(seeds.map(loadProjectState))).filter((item): item is SessionProject => !!item)

      if (cancelled) return

      const deduped = loaded.filter((item, index, arr) => arr.findIndex((entry) => entry.directory === item.directory) === index)
      setProjects(deduped)
      setActiveProjectDirectoryState((prev) => {
        if (prev && deduped.some((project) => project.directory === prev)) return prev
        return deduped[0]?.directory ?? null
      })
      setLoading(false)
    }

    void hydrate()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    persistJson(OPEN_PROJECTS_STORAGE_KEY, projects.map((project) => project.directory))
  }, [projects])

  useEffect(() => {
    persistJson(ACTIVE_PROJECT_STORAGE_KEY, activeProjectDirectory)
  }, [activeProjectDirectory])

  useEffect(() => {
    persistJson(ACTIVE_SESSIONS_STORAGE_KEY, activeSessionsByProject)
  }, [activeSessionsByProject])

  useEffect(() => {
    if (projects.length === 0) return
    setActiveProjectDirectoryState((prev) => {
      if (prev && projects.some((project) => project.directory === prev)) return prev
      return projects[0].directory
    })
  }, [projects])

  useEffect(() => {
    if (!activeProjectDirectory) return
    const project = projects.find((item) => item.directory === activeProjectDirectory)
    if (!project) return

    const activeId = activeSessionsByProject[activeProjectDirectory]
    if (activeId && project.sessions.some((session) => session.id === activeId)) return
    const fallbackId = project.sessions[0]?.id ?? null
    if ((activeId ?? null) === fallbackId) return

    setActiveSessionsByProject((prev) => ({
      ...prev,
      [activeProjectDirectory]: fallbackId,
    }))
  }, [activeProjectDirectory, activeSessionsByProject, projects])

  const refreshProject = useCallback(async (directory: string) => {
    setProjects((prev) =>
      prev.map((project) => (project.directory === directory ? { ...project, loading: true } : project)),
    )

    const next = await loadProjectState(directory)
    if (!next) {
      setProjects((prev) =>
        prev.map((project) => (project.directory === directory ? { ...project, loading: false } : project)),
      )
      return
    }

    setProjects((prev) => {
      const index = prev.findIndex((project) => project.directory === directory)
      if (index === -1) return [...prev, next]
      const copy = [...prev]
      copy[index] = next
      return copy
    })
  }, [])

  const openProject = useCallback(async (directory: string) => {
    setActiveProjectDirectoryState(directory)
    setProjects((prev) => {
      const existingIndex = prev.findIndex((project) => project.directory === directory)
      if (existingIndex !== -1) {
        const copy = [...prev]
        copy[existingIndex] = { ...copy[existingIndex], loading: true }
        return copy
      }
      return [...prev, createPendingProject(directory)]
    })

    const next = await loadProjectState(directory)
    if (!next) return null

    setProjects((prev) => {
      const existingIndex = prev.findIndex((project) => project.directory === next.directory)
      if (existingIndex === -1) return [...prev, next]
      const copy = [...prev]
      copy[existingIndex] = next
      return copy
    })
    return next
  }, [])

  const closeProject = useCallback((directory: string) => {
    const index = projects.findIndex((project) => project.directory === directory)
    if (index === -1) return

    const remaining = projects.filter((project) => project.directory !== directory)
    const fallbackDirectory = remaining[Math.min(index, remaining.length - 1)]?.directory ?? remaining[0]?.directory ?? null

    setProjects(remaining)
    setActiveProjectDirectoryState((prev) => (prev === directory ? fallbackDirectory : prev))
    setActiveSessionsByProject((prev) => {
      const next = { ...prev }
      delete next[directory]
      return next
    })
    setLastActive((prev) => (prev?.directory === directory ? null : prev))
  }, [projects])

  const setActiveSession = useCallback((directory: string, sessionId: string | null) => {
    setActiveSessionsByProject((prev) => {
      const previous = prev[directory]
      if (previous && previous !== sessionId) {
        setLastActive({ directory, sessionId: previous })
      }
      return {
        ...prev,
        [directory]: sessionId,
      }
    })
    setActiveProjectDirectoryState(directory)
  }, [])

  const create = useCallback(async (directory?: string) => {
    const targetDirectory = directory ?? activeProjectDirectory ?? projects[0]?.directory
    if (!targetDirectory) return null

    const session = await createSession(undefined, targetDirectory)
    setProjects((prev) =>
      prev.map((project) =>
        project.directory === targetDirectory
          ? { ...project, sessions: [session, ...project.sessions] }
          : project,
      ),
    )
    setActiveSession(targetDirectory, session.id)
    return session
  }, [activeProjectDirectory, projects, setActiveSession])

  const remove = useCallback(async (id: string, directory?: string) => {
    const targetDirectory = directory ?? activeProjectDirectory
    if (!targetDirectory) return

    try {
      await deleteSession(id, targetDirectory)
    } catch (err) {
      console.error('Failed to delete session', err)
    }

    setProjects((prev) =>
      prev.map((project) => {
        if (project.directory !== targetDirectory) return project
        const target = project.sessions.find((session) => session.id === id)
        return {
          ...project,
          sessions: project.sessions.filter((session) => session.id !== id),
          deletedSessions: target ? [{ ...target, visible: false }, ...project.deletedSessions] : project.deletedSessions,
        }
      }),
    )

    if (activeSessionsByProject[targetDirectory] === id) {
      const project = projects.find((item) => item.directory === targetDirectory)
      const fallbackId = project?.sessions.find((session) => session.id !== id)?.id ?? null
      setActiveSession(targetDirectory, fallbackId)
    }
  }, [activeProjectDirectory, activeSessionsByProject, projects, setActiveSession])

  const restore = useCallback(async (id: string, directory?: string) => {
    const targetDirectory = directory ?? activeProjectDirectory
    if (!targetDirectory) return

    try {
      await restoreSession(id, targetDirectory)
    } catch (err) {
      console.error('Failed to restore session', err)
      return
    }

    setProjects((prev) =>
      prev.map((project) => {
        if (project.directory !== targetDirectory) return project
        const target = project.deletedSessions.find((session) => session.id === id)
        return {
          ...project,
          sessions: target ? [{ ...target, visible: true }, ...project.sessions] : project.sessions,
          deletedSessions: project.deletedSessions.filter((session) => session.id !== id),
        }
      }),
    )

    setActiveSession(targetDirectory, id)
  }, [activeProjectDirectory, setActiveSession])

  const returnToLastSession = useCallback(() => {
    if (!lastActive) return
    setActiveSession(lastActive.directory, lastActive.sessionId)
  }, [lastActive, setActiveSession])

  const selectSessionById = useCallback((sessionId: string) => {
    for (const project of projects) {
      const match = project.sessions.find((session) => session.id === sessionId)
      if (match) {
        setActiveSession(project.directory, match.id)
        return
      }
    }
  }, [projects, setActiveSession])

  const activeProject = useMemo(
    () => projects.find((project) => project.directory === activeProjectDirectory) ?? null,
    [activeProjectDirectory, projects],
  )
  const activeId = activeProject ? activeSessionsByProject[activeProject.directory] ?? null : null
  const active = activeProject?.sessions.find((session) => session.id === activeId) ?? null
  const allSessions = useMemo(
    () => sortSessions(projects.flatMap((project) => project.sessions)),
    [projects],
  )
  const canReturnToLastSession = useMemo(
    () =>
      Boolean(
        lastActive &&
        (lastActive.directory !== activeProjectDirectory || lastActive.sessionId !== activeId) &&
        projects.some((project) => project.directory === lastActive.directory) &&
        projects.some((project) => project.sessions.some((session) => session.id === lastActive.sessionId)),
      ),
    [activeId, activeProjectDirectory, lastActive, projects],
  )

  return {
    projects,
    activeProject,
    activeProjectDirectory,
    active,
    activeId,
    allSessions,
    loading,
    setActiveProjectDirectory,
    setActiveSession,
    selectSessionById,
    openProject,
    closeProject,
    refreshProject,
    create,
    remove,
    restore,
    lastActive,
    returnToLastSession,
    canReturnToLastSession,
  }
}
