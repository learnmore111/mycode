import { useRef, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import type { Message, StreamingPart } from '../types'
import MessageBubble from './MessageBubble'
import StreamingIndicator from './StreamingIndicator'

interface Props {
  messages: Message[]
  streaming: boolean
  streamText: string
  streamParts: StreamingPart[]
  loadingHistory: boolean
  onRollback?: (turn: number, options?: { restoreSnapshot?: boolean }) => Promise<unknown> | void
}

export default function MessageList({ messages, streaming, streamText, streamParts, loadingHistory, onRollback }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamText, streamParts])

  if (loadingHistory) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-2">
          <Loader2 size={18} className="animate-spin text-accent" />
          <span className="text-sm text-ink-muted">加载历史消息...</span>
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex-1 overflow-y-auto px-4 py-6"
      role="log"
      aria-label="Conversation"
      aria-live="polite"
      aria-relevant="additions"
    >
      <div className="max-w-3xl mx-auto">
        {messages.length === 0 && !streaming && (
          <div className="text-center py-20 text-ink-muted text-sm">
            发送消息开始对话
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={msg.id}>
            {i > 0 && <div className="my-6" />}
            <MessageBubble message={msg} onRollback={onRollback} />
          </div>
        ))}

        {streaming && (
          <>
            {messages.length > 0 && <div className="my-6" />}
            <StreamingIndicator text={streamText} parts={streamParts} />
          </>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
