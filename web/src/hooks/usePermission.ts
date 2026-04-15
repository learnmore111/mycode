import { useState, useEffect, useCallback } from 'react'
import type { PermissionRequest } from '../types'
import { listPermissions, replyPermission } from '../api/permissions'

export function usePermission() {
  const [pending, setPending] = useState<PermissionRequest[]>([])

  const poll = useCallback(async () => {
    try {
      const list = await listPermissions()
      setPending(list)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    poll()
    const timer = setInterval(poll, 1000)
    return () => clearInterval(timer)
  }, [poll])

  const reply = useCallback(
    async (requestId: string, action: 'allow' | 'reject' | 'always') => {
      await replyPermission(requestId, action)
      setPending((prev) => prev.filter((p) => p.id !== requestId))
    },
    [],
  )

  return { pending, reply }
}
