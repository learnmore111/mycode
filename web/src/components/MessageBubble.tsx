import { useMemo } from 'react'
import type { Message } from '../types'
import TextContent from './TextContent'
import ToolExecution from './ToolExecution'
import MessageMeta from './MessageMeta'

interface Props {
  message: Message
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'

  const sortedParts = useMemo(() => {
    const order: Record<string, number> = { reasoning: 0, tool: 1, text: 2 }
    return [...message.parts].sort((a, b) => (order[a.type] ?? 9) - (order[b.type] ?? 9))
  }, [message.parts])

  return (
    <div className="animate-fade-in">
      {/* Role label */}
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold ${
          isUser
            ? 'bg-surface-3 text-ink-secondary'
            : 'bg-accent text-white'
        }`}>
          {isUser ? 'U' : 'A'}
        </div>
        <span className={`text-xs font-semibold ${isUser ? 'text-ink-secondary' : 'text-ink-strong'}`}>
          {isUser ? '你' : '助手'}
        </span>
      </div>

      {/* Content */}
      <div className={`pl-7 ${isUser ? 'text-ink' : 'text-ink'}`}>
        {sortedParts.map((part) => {
          switch (part.type) {
            case 'text':
              return <TextContent key={part.id} content={part.content ?? ''} />
            case 'tool':
              return <ToolExecution key={part.id} part={part} />
            case 'reasoning':
              return (
                <div key={part.id} className="text-sm text-ink-tertiary italic border-l-2 border-accent/30 pl-3.5 my-3 leading-relaxed">
                  {part.content}
                </div>
              )
            default:
              return null
          }
        })}
      </div>

      {/* Metadata */}
      {!isUser && message.tokens && (
        <div className="pl-7">
          <MessageMeta message={message} />
        </div>
      )}
    </div>
  )
}
