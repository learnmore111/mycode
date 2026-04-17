import { useState, useEffect, useCallback, useRef } from 'react'
import type { PermissionRequest } from '../types'
import { listPermissions, replyPermission } from '../api/permissions'

/**
 * Hook for managing permission requests.
 *
 * Uses SSE (Server-Sent Events) via /event for real-time permission notifications
 * when available, with a fallback to polling every 2 seconds.
 *
 * Flow:
 *   1. Backend tool execution hits an "ask" permission rule
 *   2. PermissionManager publishes "permission.asked" event to the bus
 *   3. SSE /event stream delivers it to the frontend
 *   4. This hook updates state → PermissionModal is shown
 *   5. User clicks allow/reject/always → POST /permission/:id
 *   6. Backend PermissionManager.reply() resolves the awaiting future
 *   7. Tool execution resumes
 */
export function usePermission() {
  const [pending, setPending] = useState<PermissionRequest[]>([])
  const eventSourceRef = useRef<EventSource | null>(null)

  // Fetch full pending list (used on init and as fallback)
  const poll = useCallback(async () => {
    try {
      const list = await listPermissions()
      setPending(list)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    // Initial fetch to catch any already-pending requests
    poll()

    // Try SSE for real-time updates
    let fallbackTimer: ReturnType<typeof setInterval> | null = null

    try {
      const es = new EventSource('/event?event_type=*')
      eventSourceRef.current = es

      es.onmessage = () => {
        // Generic messages — ignore
      }

      // Listen for permission events
      es.addEventListener('permission.asked', (event) => {
        try {
          const data = JSON.parse(event.data)
          const req: PermissionRequest = {
            id: data.id,
            session_id: data.session_id,
            permission: data.permission,
            patterns: data.patterns || [],
            metadata: data.metadata || {},
          }
          setPending((prev) => {
            // Avoid duplicates
            if (prev.some((p) => p.id === req.id)) return prev
            return [...prev, req]
          })
        } catch {
          // malformed event
        }
      })

      es.addEventListener('permission.replied', (event) => {
        try {
          const data = JSON.parse(event.data)
          const requestId = data.request_id
          if (requestId) {
            setPending((prev) => prev.filter((p) => p.id !== requestId))
          }
        } catch {
          // malformed event
        }
      })

      es.onerror = () => {
        // SSE disconnected — fall back to polling
        if (!fallbackTimer) {
          fallbackTimer = setInterval(poll, 2000)
        }
      }

      es.onopen = () => {
        // SSE reconnected — stop polling fallback
        if (fallbackTimer) {
          clearInterval(fallbackTimer)
          fallbackTimer = null
        }
        // Refresh list in case we missed events during disconnect
        poll()
      }
    } catch {
      // EventSource not available — use polling
      fallbackTimer = setInterval(poll, 2000)
    }

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
      if (fallbackTimer) {
        clearInterval(fallbackTimer)
      }
    }
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
