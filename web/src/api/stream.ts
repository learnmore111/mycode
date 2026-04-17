import type { SSEEvent } from '../types'

export interface StreamCallbacks {
  onEvent: (event: SSEEvent) => void
  onError?: (error: Error) => void
  onDone?: () => void
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
            if (currentEvent && dataStr) {
              try {
                const data = JSON.parse(dataStr)
                callbacks.onEvent({ type: currentEvent as SSEEvent['type'], data })
              } catch {
                // skip malformed JSON
              }
            }
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
