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
}

export default function MessageList({ messages, streaming, streamText, streamParts, loadingHistory }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamText, streamParts])

  if (loadingHistory) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 size={24} className="animate-spin text-white/30" />
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      {messages.length === 0 && !streaming && (
        <div className="text-center py-16 text-white/30 text-sm">
          发送消息开始对话
        </div>
      )}

      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}

      {streaming && (
        <StreamingIndicator text={streamText} parts={streamParts} />
      )}

      <div ref={bottomRef} />
    </div>
  )
}
