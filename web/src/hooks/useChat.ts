import { useState, useCallback, useRef } from 'react'
import type { Message, StreamingPart, SSEEvent } from '../types'
import { getMessages, abortSession } from '../api/sessions'
import { streamMessage } from '../api/stream'

export function useChat(sessionId: string | null) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamParts, setStreamParts] = useState<StreamingPart[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  const loadHistory = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      return
    }
    setLoadingHistory(true)
    try {
      const msgs = await getMessages(sessionId)
      setMessages(msgs)
    } catch (err) {
      console.error('Failed to load messages', err)
    } finally {
      setLoadingHistory(false)
    }
  }, [sessionId])

  const send = useCallback(
    (
      text: string,
      options?: { model?: string; agent?: string },
    ) => {
      if (!sessionId || !text.trim()) return

      // Optimistic user message
      const userMsg: Message = {
        id: `tmp-${Date.now()}`,
        sessionId,
        role: 'user',
        parts: [{ id: `tmp-p-${Date.now()}`, type: 'text', content: text, time: { created: Date.now() } }],
        time: { created: Date.now() },
      }
      setMessages((prev) => [...prev, userMsg])
      setStreaming(true)
      setStreamText('')
      setStreamParts([])
      setError(null)

      const controller = streamMessage(
        sessionId,
        [{ type: 'text', content: text }],
        {
          onEvent: (event: SSEEvent) => {
            switch (event.type) {
              case 'text_delta':
                setStreamText((prev) => prev + (event.data.content ?? ''))
                break

              case 'tool_start':
                setStreamParts((prev) => [
                  ...prev,
                  {
                    id: (event.data.call_id as string) ?? `tool-${Date.now()}`,
                    type: 'tool',
                    content: '',
                    tool: event.data.tool as string,
                    toolCallId: event.data.call_id as string,
                    state: { status: 'running', input: event.data.input },
                  },
                ])
                break

              case 'tool_running':
                setStreamParts((prev) =>
                  prev.map((p) =>
                    p.toolCallId === event.data.call_id
                      ? { ...p, state: { ...p.state, status: 'running', input: event.data.input } }
                      : p,
                  ),
                )
                break

              case 'tool_done':
                setStreamParts((prev) =>
                  prev.map((p) =>
                    p.toolCallId === event.data.call_id
                      ? {
                          ...p,
                          content: (event.data.output as string) ?? '',
                          state: {
                            status: event.data.status as string,
                            input: event.data.input,
                            output: event.data.output as string,
                            error: event.data.error as string,
                          },
                        }
                      : p,
                  ),
                )
                break

              case 'error':
                setError(event.data.message as string)
                break

              case 'done':
                // Reload full messages from DB to get persisted versions
                if (sessionId) {
                  getMessages(sessionId).then((msgs) => {
                    setMessages(msgs)
                  })
                }
                setStreaming(false)
                setStreamText('')
                setStreamParts([])
                break
            }
          },
          onError: (err) => {
            setError(err.message)
            setStreaming(false)
          },
          onDone: () => {
            // SSE stream closed - if 'done' event wasn't received, reload anyway
            if (sessionId) {
              getMessages(sessionId).then((msgs) => {
                setMessages(msgs)
              })
            }
            setStreaming(false)
            setStreamText('')
            setStreamParts([])
          },
        },
        options,
      )

      controllerRef.current = controller
    },
    [sessionId],
  )

  const abort = useCallback(async () => {
    if (sessionId) {
      await abortSession(sessionId)
    }
    controllerRef.current?.abort()
    setStreaming(false)
  }, [sessionId])

  return {
    messages,
    streaming,
    streamText,
    streamParts,
    error,
    loadingHistory,
    loadHistory,
    send,
    abort,
  }
}
