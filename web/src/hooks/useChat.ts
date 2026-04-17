import { useState, useCallback, useRef, useEffect } from 'react'
import type { Message, StreamingPart, SSEEvent, ContextSnapshot } from '../types'
import { getMessages, getContextSnapshot, abortSession } from '../api/sessions'
import { streamMessage } from '../api/stream'
import { buildResumePrompt } from '../utils/sessionInsights'

interface ChatOptions {
  model?: string
  agent?: string
}

interface PausedRunState {
  sessionId: string
  lastUserText: string
  partialText?: string
  pausedAt: number
  model?: string
  agent?: string
}

const PAUSED_RUN_STORAGE_KEY = 'mycode.pausedRuns'

function readPausedRuns(): Record<string, PausedRunState> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(PAUSED_RUN_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Record<string, PausedRunState>) : {}
  } catch {
    return {}
  }
}

function writePausedRuns(next: Record<string, PausedRunState>) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(PAUSED_RUN_STORAGE_KEY, JSON.stringify(next))
}

export function useChat(sessionId: string | null) {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamParts, setStreamParts] = useState<StreamingPart[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [contextSnapshot, setContextSnapshot] = useState<ContextSnapshot | null>(null)
  const [pausedRun, setPausedRun] = useState<PausedRunState | null>(null)
  const [status, setStatus] = useState<'idle' | 'streaming' | 'paused'>('idle')

  const controllerRef = useRef<AbortController | null>(null)
  const lastUserTextRef = useRef('')
  const lastOptionsRef = useRef<ChatOptions>({})
  const streamTextRef = useRef('')
  const streamPartsRef = useRef<StreamingPart[]>([])

  const clearPausedRun = useCallback((targetSessionId: string | null) => {
    if (!targetSessionId) {
      setPausedRun(null)
      return
    }

    const next = readPausedRuns()
    delete next[targetSessionId]
    writePausedRuns(next)

    setPausedRun((current) => (current?.sessionId === targetSessionId ? null : current))
  }, [])

  const savePausedRun = useCallback((draft: PausedRunState) => {
    const next = readPausedRuns()
    next[draft.sessionId] = draft
    writePausedRuns(next)
    setPausedRun(draft)
  }, [])

  useEffect(() => {
    if (!sessionId) {
      setPausedRun(null)
      setStatus('idle')
      return
    }

    const stored = readPausedRuns()[sessionId] ?? null
    setPausedRun(stored)
    setStatus(stored ? 'paused' : 'idle')
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
    (text: string, options?: ChatOptions) => {
      if (!sessionId || !text.trim()) return

      const trimmedText = text.trim()
      lastUserTextRef.current = trimmedText
      lastOptionsRef.current = options ?? {}
      clearPausedRun(sessionId)

      const userMsg: Message = {
        id: `tmp-${Date.now()}`,
        sessionId,
        role: 'user',
        parts: [{ id: `tmp-p-${Date.now()}`, type: 'text', content: trimmedText, time: { created: Date.now() } }],
        time: { created: Date.now() },
      }

      setMessages((prev) => [...prev, userMsg])
      setStreaming(true)
      setStatus('streaming')
      setError(null)
      streamTextRef.current = ''
      streamPartsRef.current = []
      setStreamText('')
      setStreamParts([])

      const controller = streamMessage(
        sessionId,
        [{ type: 'text', content: trimmedText }],
        {
          onEvent: (event: SSEEvent) => {
            switch (event.type) {
              case 'text_delta':
                setStreamText((prev) => {
                  const next = prev + (event.data.content ?? '')
                  streamTextRef.current = next
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
                setStatus('idle')
                break

              case 'done': {
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
                        usage_percent: realLimit > 0 ? Math.round((1000 * realUsed) / realLimit) / 10 : 0,
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
                clearPausedRun(sessionId)
                setStatus('idle')
                resetStreamingState()
                break
              }
            }
          },
          onError: (err) => {
            setError(err.message)
            setStatus(pausedRun ? 'paused' : 'idle')
            resetStreamingState()
          },
          onDone: () => {
            reloadPersistedState().catch((err) => {
              console.error('Failed to reload persisted session state', err)
            })
            setStatus('idle')
            resetStreamingState()
          },
        },
        options,
      )

      controllerRef.current = controller
    },
    [clearPausedRun, pausedRun, reloadPersistedState, resetStreamingState, sessionId],
  )

  const abort = useCallback(async () => {
    if (!sessionId) return

    const lastUserText = lastUserTextRef.current.trim()
    if (lastUserText) {
      savePausedRun({
        sessionId,
        lastUserText,
        partialText: streamTextRef.current.trim() || undefined,
        pausedAt: Date.now(),
        model: lastOptionsRef.current.model,
        agent: lastOptionsRef.current.agent,
      })
      setStatus('paused')
    }

    try {
      await abortSession(sessionId)
    } catch (err) {
      console.error('Failed to abort session', err)
    }

    controllerRef.current?.abort()
    resetStreamingState()
  }, [resetStreamingState, savePausedRun, sessionId])

  const resume = useCallback(() => {
    if (!sessionId || !pausedRun) return

    const prompt = buildResumePrompt(pausedRun.lastUserText, pausedRun.partialText)
    clearPausedRun(sessionId)
    send(prompt, {
      model: pausedRun.model,
      agent: pausedRun.agent,
    })
  }, [clearPausedRun, pausedRun, send, sessionId])

  const dismissPausedRun = useCallback(() => {
    if (!sessionId) return
    clearPausedRun(sessionId)
    setStatus('idle')
  }, [clearPausedRun, sessionId])

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
  }
}
