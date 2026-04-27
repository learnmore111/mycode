import { useState, useCallback, useRef, useEffect } from 'react'
import type { ContextSnapshot, Message, PausedRun, SessionCodeChange, SSEEvent, StreamingPart } from '../types'
import {
  abortSession,
  clearPausedRun as clearPausedRunRemote,
  getContextSnapshot,
  getMessages,
  getPausedRun,
  getSessionCodeChanges,
  pauseSession,
  rollbackToTurn as rollbackToTurnRemote,
} from '../api/sessions'
import { streamMessage, streamResume } from '../api/stream'

interface ChatOptions {
  model?: string
  agent?: string
}

export function useChat(sessionId: string | null) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamParts, setStreamParts] = useState<StreamingPart[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [contextSnapshot, setContextSnapshot] = useState<ContextSnapshot | null>(null)
  const [pausedRun, setPausedRun] = useState<PausedRun | null>(null)
  const [codeChanges, setCodeChanges] = useState<SessionCodeChange[]>([])
  const [status, setStatus] = useState<'idle' | 'streaming' | 'paused'>('idle')

  const controllerRef = useRef<AbortController | null>(null)
  const lastUserTextRef = useRef('')
  const lastOptionsRef = useRef<ChatOptions>({})
  const streamTextRef = useRef('')
  const streamPartsRef = useRef<StreamingPart[]>([])

  useEffect(() => {
    if (!sessionId) {
      setPausedRun(null)
      setCodeChanges([])
      setStatus('idle')
      return
    }

    getPausedRun(sessionId)
      .then((state) => {
        setPausedRun(state)
        setStatus(state ? 'paused' : 'idle')
      })
      .catch((err) => {
        console.error('Failed to load paused run state', err)
      })
  }, [sessionId])

  const resetStreamingState = useCallback(() => {
    streamTextRef.current = ''
    streamPartsRef.current = []
    setStreamText('')
    setStreamParts([])
    setStreaming(false)
    controllerRef.current = null
  }, [])

  const reloadPersistedState = useCallback(async () => {
    if (!sessionId) return

    const [msgs, snapshot, paused, changes] = await Promise.all([
      getMessages(sessionId),
      getContextSnapshot(sessionId).catch((err) => {
        console.error('Failed to load context snapshot', err)
        return null
      }),
      getPausedRun(sessionId).catch((err) => {
        console.error('Failed to load paused run state', err)
        return null
      }),
      getSessionCodeChanges(sessionId).catch((err) => {
        console.error('Failed to load session code changes', err)
        return []
      }),
    ])

    setMessages(msgs)
    setPausedRun(paused)
    setCodeChanges(changes)
    setStatus(paused ? 'paused' : 'idle')
    if (snapshot) {
      setContextSnapshot(snapshot)
    }
  }, [sessionId])

  const loadHistory = useCallback(async () => {
    if (!sessionId) {
      setMessages([])
      setContextSnapshot(null)
      setCodeChanges([])
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

  const applyStreamEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case 'reasoning_delta':
          setStreamParts((prev) => {
            const delta = (event.data.content as string) ?? ''
            const lastPart = prev[prev.length - 1]
            if (lastPart?.type === 'reasoning') {
              const next = prev.map((part, index) =>
                index === prev.length - 1
                  ? { ...part, content: part.content + delta }
                  : part,
              )
              streamPartsRef.current = next
              return next
            }
            const next = [
              ...prev,
              {
                id: `reasoning-${Date.now()}`,
                type: 'reasoning' as const,
                content: delta,
              },
            ]
            streamPartsRef.current = next
            return next
          })
          break

        case 'text_delta':
          setStreamText((prev) => {
            const next = prev + (event.data.content ?? '')
            streamTextRef.current = next
            return next
          })
          setStreamParts((prev) => {
            const delta = (event.data.content as string) ?? ''
            const lastPart = prev[prev.length - 1]
            if (lastPart?.type === 'text') {
              const next = prev.map((part, index) =>
                index === prev.length - 1
                  ? { ...part, content: part.content + delta }
                  : part,
              )
              streamPartsRef.current = next
              return next
            }
            const next = [
              ...prev,
              {
                id: `text-${Date.now()}`,
                type: 'text' as const,
                content: delta,
              },
            ]
            streamPartsRef.current = next
            return next
          })
          break

        case 'tool_start':
          setStreamParts((prev) => {
            const next = [
              ...prev,
              {
                id: (event.data.call_id as string) ?? `tool-${Date.now()}`,
                type: 'tool' as const,
                content: '',
                tool: event.data.tool as string,
                toolCallId: event.data.call_id as string,
                state: { status: 'running', input: event.data.input },
              },
            ]
            streamPartsRef.current = next
            return next
          })
          break

        case 'tool_running':
          setStreamParts((prev) => {
            const next = prev.map((p) =>
              p.toolCallId === event.data.call_id
                ? { ...p, state: { ...p.state, status: 'running', input: event.data.input } }
                : p,
            )
            streamPartsRef.current = next
            return next
          })
          break

        case 'tool_done':
          setStreamParts((prev) => {
            const next = prev.map((p) =>
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
            )
            streamPartsRef.current = next
            return next
          })
          break

        case 'context_snapshot':
          setContextSnapshot(event.data as unknown as ContextSnapshot)
          break

        case 'compact': {
          const metrics = event.data as {
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
          break
        }

        case 'error':
          setError(event.data.message as string)
          break

        case 'done': {
          const tokens = event.data.tokens as
            | { input?: number; output?: number; cache_read?: number; cache_write?: number; reasoning?: number }
            | undefined
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
                  usage_percent: realLimit > 0 ? Math.round((1000 * realUsed) / realLimit) / 10 : 0,
                },
                actual_usage: {
                  input_tokens: tokens.input ?? 0,
                  output_tokens: tokens.output ?? 0,
                  cache_read_tokens: tokens.cache_read ?? 0,
                  cache_write_tokens: tokens.cache_write ?? 0,
                  reasoning_tokens: tokens.reasoning ?? 0,
                  total_cost: (event.data.cost as number) ?? 0,
                  raw_usage: (event.data.raw_usage as Record<string, unknown> | null | undefined) ?? null,
                },
              }
            })
          }
          break
        }
      }
    },
    [],
  )

  const finalizeStream = useCallback(() => {
    reloadPersistedState().catch((err) => {
      console.error('Failed to reload persisted session state', err)
    })
    resetStreamingState()
  }, [reloadPersistedState, resetStreamingState])

  const send = useCallback(
    (text: string, options?: ChatOptions) => {
      if (!sessionId || !text.trim()) return

      const trimmedText = text.trim()
      lastUserTextRef.current = trimmedText
      lastOptionsRef.current = options ?? {}
      setPausedRun(null)
      setError(null)
      setStatus('streaming')
      setStreaming(true)
      streamTextRef.current = ''
      streamPartsRef.current = []
      setStreamText('')
      setStreamParts([])
      void clearPausedRunRemote(sessionId).catch((err) => {
        console.error('Failed to clear paused run state', err)
      })

      const userMsg: Message = {
        id: `tmp-${Date.now()}`,
        sessionId,
        role: 'user',
        parts: [{ id: `tmp-p-${Date.now()}`, type: 'text', content: trimmedText, time: { created: Date.now() } }],
        time: { created: Date.now() },
      }

      setMessages((prev) => [...prev, userMsg])

      controllerRef.current = streamMessage(
        sessionId,
        [{ type: 'text', content: trimmedText }],
        {
          onEvent: applyStreamEvent,
          onError: (err) => {
            setError(err.message)
            finalizeStream()
          },
          onDone: finalizeStream,
        },
        options,
      )
    },
    [applyStreamEvent, finalizeStream, sessionId],
  )

  const abort = useCallback(async () => {
    if (!sessionId) return

    const lastUserText = lastUserTextRef.current.trim()
    if (lastUserText) {
      try {
        const result = await pauseSession(sessionId, {
          lastUserText,
          partialText: streamTextRef.current.trim() || undefined,
          pausedAt: Date.now(),
          model: lastOptionsRef.current.model,
          agent: lastOptionsRef.current.agent,
        })
        setPausedRun(result.state)
        setStatus(result.state ? 'paused' : 'idle')
      } catch (err) {
        console.error('Failed to persist paused run state', err)
      }
    } else {
      try {
        await abortSession(sessionId)
      } catch (err) {
        console.error('Failed to abort session', err)
      }
    }

    controllerRef.current?.abort()
    resetStreamingState()
  }, [resetStreamingState, sessionId])

  const resume = useCallback(() => {
    if (!sessionId || !pausedRun) return

    setError(null)
    setStatus('streaming')
    setStreaming(true)
    setStreamText('')
    setStreamParts([])
    setPausedRun(null)

    controllerRef.current = streamResume(sessionId, {
      onEvent: applyStreamEvent,
      onError: (err) => {
        setError(err.message)
        finalizeStream()
      },
      onDone: finalizeStream,
    })
  }, [applyStreamEvent, finalizeStream, pausedRun, sessionId])

  const dismissPausedRun = useCallback(async () => {
    if (!sessionId) return
    try {
      await clearPausedRunRemote(sessionId)
    } catch (err) {
      console.error('Failed to clear paused run state', err)
    }
    setPausedRun(null)
    setStatus('idle')
  }, [sessionId])

  const rollbackToTurn = useCallback(
    async (turn: number, options?: { restoreSnapshot?: boolean }) => {
      if (!sessionId) return null
      try {
        const result = await rollbackToTurnRemote(sessionId, turn, options)
        // Refresh both transcript and code-changes after rollback.
        await reloadPersistedState()
        return result
      } catch (err) {
        const msg = err instanceof Error ? err.message : '回退失败'
        setError(msg)
        throw err
      }
    },
    [reloadPersistedState, sessionId],
  )

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
    resume,
    dismissPausedRun,
    pausedRun,
    status,
    contextSnapshot,
    codeChanges,
    rollbackToTurn,
  }
}
