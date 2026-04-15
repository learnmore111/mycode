import { apiFetch } from './client'
import type { PermissionRequest } from '../types'

export async function listPermissions(): Promise<PermissionRequest[]> {
  return apiFetch<PermissionRequest[]>('/permission')
}

export async function replyPermission(
  requestId: string,
  reply: 'allow' | 'reject' | 'always',
  message?: string,
): Promise<void> {
  await apiFetch(`/permission/${requestId}`, {
    method: 'POST',
    body: JSON.stringify({ reply, message }),
  })
}
