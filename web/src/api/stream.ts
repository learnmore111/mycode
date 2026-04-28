import type { SSEEvent, SSEEventType } from '../types'

export interface StreamCallbacks {
  onEvent: (event: SSEEvent) => void
  onError?: (error: Error) => void
  onDone?: () => void
  /**
   * Optional callback fired whenever a malformed SSE frame (bad JSON or
   * unknown event type) is seen so the UI can surface the fact that
   * data was dropped instead of silently swallowing it.
   */
  onInvalidFrame?: (reason: string, raw: string) => void
}

/**
 * Known event types kept in sync with the Python side's `PromptEvent`
 * taxonomy. Anything outside this set is treated as malformed.
 */
const KNOWN_EVENT_TYPES = new Set<SSEEventType>([
  'started',
  'reasoning_delta',
  'text_delta',
  'tool_start',
  'tool_running',
  'tool_done',
  'error',
  'compact',
  'guard_warn',
  'guard_stop',
  'context_snapshot',
  'done',
])

function validateSSEEvent(
  eventName: string,
  payload: unknown,
): SSEEvent | { invalid: string } {
  if (!KNOWN_EVENT_TYPES.has(eventName as SSEEventType)) {
    return { invalid: `unknown event type: ${eventName}` }
  }
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    return { invalid: `payload must be an object, got ${typeof payload}` }
  }
  return { type: eventName as SSEEventType, data: payload as Record<string, unknown> }
}

function streamRequest(path: string, callbacks: StreamCallbacks, init?: RequestInit): AbortController {
  const controller = new AbortController()

  fetch(path, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        throw new Error(`Stream error: ${res.status}`)
      }
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim()
            if (!currentEvent || !dataStr) continue
            let parsed: unknown
            try {
              parsed = JSON.parse(dataStr)
            } catch (e) {
              callbacks.onInvalidFrame?.(`JSON parse failed: ${(e as Error).message}`, dataStr)
              continue
            }
            const validated = validateSSEEvent(currentEvent, parsed)
            if ('invalid' in validated) {
              callbacks.onInvalidFrame?.(validated.invalid, dataStr)
              continue
            }
            callbacks.onEvent(validated)
          } else if (line === '') {
            currentEvent = ''
          }
        }
      }

      callbacks.onDone?.()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        callbacks.onError?.(err)
      }
    })

  return controller
}

/**
 * Send a message via POST and stream SSE events back.
 * Returns an AbortController to cancel the stream.
 */
export function streamMessage(
  sessionId: string,
  parts: Array<{ type: string; content: string }>,
  callbacks: StreamCallbacks,
  options?: { model?: string; agent?: string },
): AbortController {
  return streamRequest(`/session/${sessionId}/message`, callbacks, {
    method: 'POST',
    body: JSON.stringify({
      parts,
      model: options?.model,
      agent: options?.agent,
    }),
  })
}

export function streamResume(sessionId: string, callbacks: StreamCallbacks): AbortController {
  return streamRequest(`/session/${sessionId}/resume`, callbacks, {
    method: 'POST',
  })
}
