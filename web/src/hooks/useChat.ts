import { useState, useCallback, useRef } from 'react'
import type { Message, StreamingPart, SSEEvent, ContextSnapshot } from '../types'
import { getMessages, getContextSnapshot, abortSession } from '../api/sessions'
import { streamMessage } from '../api/stream'

export function useChat(sessionId: string | null) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamParts, setStreamParts] = useState<StreamingPart[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [contextSnapshot, setContextSnapshot] = useState<ContextSnapshot | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  const reloadPersistedState = useCallback(async () => {
    if (!sessionId) return

    const [msgs, snapshot] = await Promise.all([
      getMessages(sessionId),
      getContextSnapshot(sessionId).catch((err) => {
        console.error('Failed to load context snapshot', err)
        return null
      }),
    ])

    setMessages(msgs)
    if (snapshot) {
      setContextSnapshot(snapshot)
    }
  }, [sessionId])

  const loadHistory = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      setContextSnapshot(null)
      return
    }
    setLoadingHistory(true)
    try {
      await reloadPersistedState()
    } catch (err) {
      console.error('Failed to load messages', err)
    } finally {
      setLoadingHistory(false)
    }
  }, [sessionId, reloadPersistedState])

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

              case 'context_snapshot':
                setContextSnapshot(event.data as unknown as ContextSnapshot)
                break

              case 'compact': {
                // Context compaction occurred - old messages were summarized and removed.
                const metrics = event.data as {
                  session_id?: string
                  old_message_count?: number
                  old_message_tokens?: number
                  summary_length?: number
                  removed_turn_count?: number
                }
                console.debug('Compaction metrics:', {
                  removedMessages: metrics.old_message_count,
                  freedTokens: metrics.old_message_tokens,
                  summaryLength: metrics.summary_length,
                  removedTurns: metrics.removed_turn_count,
                })
                // The compaction summary will appear in the next context_snapshot.
                break
              }


              case 'error':
                setError(event.data.message as string)
                break

              case 'done':
                // Update context snapshot with real API token data
                {
                  const tokens = event.data.tokens as { input?: number; output?: number; cache_read?: number; cache_write?: number; reasoning?: number } | undefined
                  const ctx = event.data.context as { used?: number; limit?: number } | undefined
                  if (tokens && ctx && ctx.limit) {
                    const realUsed = ctx.used ?? tokens.input ?? 0
                    const realLimit = ctx.limit
                    setContextSnapshot((prev) => {
                      if (!prev) return prev
                      return {
                        ...prev,
                        summary: {
                          ...prev.summary,
                          total_estimated_tokens: realUsed,
                          context_limit: realLimit,
                          usage_percent: realLimit > 0 ? Math.round(1000 * realUsed / realLimit) / 10 : 0,
                        },
                        actual_usage: {
                          input_tokens: tokens.input ?? 0,
                          output_tokens: tokens.output ?? 0,
                          cache_read_tokens: tokens.cache_read ?? 0,
                          cache_write_tokens: tokens.cache_write ?? 0,
                          reasoning_tokens: tokens.reasoning ?? 0,
                          total_cost: (event.data.cost as number) ?? 0,
                        },
                      }
                    })
                  }
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
            // SSE stream closed after backend persistence, so reload the
            // canonical messages + context snapshot from the database.
            reloadPersistedState().catch((err) => {
              console.error('Failed to reload persisted session state', err)
            })
            setStreaming(false)
            setStreamText('')
            setStreamParts([])
          },
        },
        options,
      )

      controllerRef.current = controller
    },
    [sessionId, reloadPersistedState],
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
    contextSnapshot,
  }
}
