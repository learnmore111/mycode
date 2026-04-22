import { apiFetch } from './client'
import type { ContextSnapshot, Message, PausedRun, Session, SessionCodeChange } from '../types'

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

export async function listSessions(): Promise<Session[]> {
  return apiFetch<Session[]>('/session')
}

export async function createSession(title?: string): Promise<Session> {
  return apiFetch<Session>('/session', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

export async function getSession(id: string): Promise<Session> {
  return apiFetch<Session>(`/session/${id}`)
}

export async function deleteSession(id: string): Promise<void> {
  await apiFetch(`/session/${id}`, { method: 'DELETE' })
}

export async function restoreSession(id: string): Promise<void> {
  await apiFetch(`/session/${id}/restore`, { method: 'POST' })
}

export async function listDeletedSessions(): Promise<Session[]> {
  return apiFetch<Session[]>('/session/deleted')
}

export async function getMessages(sessionId: string): Promise<Message[]> {
  return apiFetch<Message[]>(`/session/${sessionId}/messages`)
}

export async function getContextSnapshot(sessionId: string): Promise<ContextSnapshot> {
  return apiFetch<ContextSnapshot>(`/session/${sessionId}/context`)
}

export async function getSessionCodeChanges(sessionId: string): Promise<SessionCodeChange[]> {
  return apiFetch<SessionCodeChange[]>(`/session/${sessionId}/changes`)
}

export async function getPausedRun(sessionId: string): Promise<PausedRun | null> {
  const result = await apiFetch<PausedRunResponse>(`/session/${sessionId}/pause`)
  return result.state
}

export async function pauseSession(sessionId: string, payload: PauseSessionPayload): Promise<PauseSessionResponse> {
  return apiFetch<PauseSessionResponse>(`/session/${sessionId}/pause`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function clearPausedRun(sessionId: string): Promise<void> {
  await apiFetch(`/session/${sessionId}/pause`, { method: 'DELETE' })
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
): Promise<RollbackResult> {
  return apiFetch<RollbackResult>(`/session/${sessionId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({
      turn,
      restore_snapshot: options?.restoreSnapshot ?? true,
    }),
  })
}
