import { apiFetch } from './client'
import type { Session, Message } from '../types'

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

export async function abortSession(sessionId: string): Promise<void> {
  await apiFetch(`/session/${sessionId}/abort`, { method: 'POST' })
}
