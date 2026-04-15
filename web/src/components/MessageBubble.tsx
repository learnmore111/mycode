import { User, Bot } from 'lucide-react'
import type { Message } from '../types'
import TextContent from './TextContent'
import ToolExecution from './ToolExecution'
import MessageMeta from './MessageMeta'

interface Props {
  message: Message
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-white/10 flex items-center justify-center mt-1">
          <Bot size={14} className="text-blue-300" />
        </div>
      )}

      <div className={`max-w-[80%] ${isUser ? 'order-first' : ''}`}>
        <div
          className={`rounded-2xl px-4 py-2.5 ${
            isUser
              ? 'bg-blue-500/25 text-white border border-blue-400/15 rounded-br-md'
              : 'bg-white/8 text-gray-100 border border-white/8 rounded-bl-md'
          }`}
        >
          {message.parts.map((part) => {
            switch (part.type) {
              case 'text':
                return <TextContent key={part.id} content={part.content ?? ''} />
              case 'tool':
                return <ToolExecution key={part.id} part={part} />
              case 'reasoning':
                return (
                  <div key={part.id} className="text-xs text-white/40 italic border-l-2 border-white/15 pl-2 my-1">
                    {part.content}
                  </div>
                )
              default:
                return null
            }
          })}
        </div>

        {!isUser && message.tokens && <MessageMeta message={message} />}
      </div>

      {isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-blue-500/30 flex items-center justify-center mt-1">
          <User size={14} className="text-blue-200" />
        </div>
      )}
    </div>
  )
}
