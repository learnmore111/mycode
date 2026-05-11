import { apiFetch } from './client'
import type { ContextSnapshot, Message, PausedRun, Session, SessionCodeChange } from '../types'

function buildQuery(directory?: string): string {
  const params = new URLSearchParams()
  if (directory) params.set('directory', directory)
  const query = params.toString()
  return query ? `?${query}` : ''
}

interface PauseSessionPayload {
  lastUserText: string
  partialText?: string
  pausedAt?: number
  model?: string
  agent?: string
}

interface PauseSessionResponse {
  ok: boolean
  aborted: boolean
  paused: boolean
  state: PausedRun | null
}

interface PausedRunResponse {
  paused: boolean
  state: PausedRun | null
}

export async function listSessions(directory?: string): Promise<Session[]> {
  return apiFetch<Session[]>(`/session${buildQuery(directory)}`)
}

export async function createSession(title?: string, directory?: string): Promise<Session> {
  return apiFetch<Session>(`/session${buildQuery(directory)}`, {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

export async function getSession(id: string, directory?: string): Promise<Session> {
  return apiFetch<Session>(`/session/${id}${buildQuery(directory)}`)
}

export async function deleteSession(id: string, directory?: string): Promise<void> {
  await apiFetch(`/session/${id}${buildQuery(directory)}`, { method: 'DELETE' })
}

export async function restoreSession(id: string, directory?: string): Promise<void> {
  await apiFetch(`/session/${id}/restore${buildQuery(directory)}`, { method: 'POST' })
}

export async function listDeletedSessions(directory?: string): Promise<Session[]> {
  return apiFetch<Session[]>(`/session/deleted${buildQuery(directory)}`)
}

export async function getMessages(sessionId: string, directory?: string): Promise<Message[]> {
  return apiFetch<Message[]>(`/session/${sessionId}/messages${buildQuery(directory)}`)
}

export async function getContextSnapshot(sessionId: string, directory?: string): Promise<ContextSnapshot> {
  return apiFetch<ContextSnapshot>(`/session/${sessionId}/context${buildQuery(directory)}`)
}

export async function getSessionCodeChanges(sessionId: string, directory?: string): Promise<SessionCodeChange[]> {
  return apiFetch<SessionCodeChange[]>(`/session/${sessionId}/changes${buildQuery(directory)}`)
}

export async function getPausedRun(sessionId: string, directory?: string): Promise<PausedRun | null> {
  const result = await apiFetch<PausedRunResponse>(`/session/${sessionId}/pause${buildQuery(directory)}`)
  return result.state
}

export async function pauseSession(
  sessionId: string,
  payload: PauseSessionPayload,
  directory?: string,
): Promise<PauseSessionResponse> {
  return apiFetch<PauseSessionResponse>(`/session/${sessionId}/pause${buildQuery(directory)}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function clearPausedRun(sessionId: string, directory?: string): Promise<void> {
  await apiFetch(`/session/${sessionId}/pause${buildQuery(directory)}`, { method: 'DELETE' })
}

export async function abortSession(sessionId: string): Promise<void> {
  await apiFetch(`/session/${sessionId}/abort`, { method: 'POST' })
}

// ---- Rollback ----
export interface RollbackResult {
  kept: number
  removed: number
  snapshot_ref: string | null
  restored: boolean
}

export async function rollbackToTurn(
  sessionId: string,
  turn: number,
  options?: { restoreSnapshot?: boolean },
  directory?: string,
): Promise<RollbackResult> {
  return apiFetch<RollbackResult>(`/session/${sessionId}/rollback${buildQuery(directory)}`, {
    method: 'POST',
    body: JSON.stringify({
      turn,
      restore_snapshot: options?.restoreSnapshot ?? true,
    }),
  })
}
