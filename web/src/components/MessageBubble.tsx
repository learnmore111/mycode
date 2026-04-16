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
    <div className="flex gap-3">
      {/* Avatar */}
      <div className="flex-shrink-0 mt-0.5">
        {isUser ? (
          <div className="w-7 h-7 rounded-md bg-surface-3 flex items-center justify-center">
            <User size={14} className="text-text-secondary" />
          </div>
        ) : (
          <div className="w-7 h-7 rounded-md bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center">
            <Bot size={14} className="text-white" />
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 max-w-3xl">
        {/* Role label */}
        <div className="text-xs font-medium text-text-tertiary mb-1">
          {isUser ? '你' : 'AI 助手'}
        </div>

        {/* Message content */}
        <div className="text-sm text-text-primary">
          {message.parts.map((part) => {
            switch (part.type) {
              case 'text':
                return <TextContent key={part.id} content={part.content ?? ''} />
              case 'tool':
                return <ToolExecution key={part.id} part={part} />
              case 'reasoning':
                return (
                  <div key={part.id} className="text-sm text-text-tertiary italic border-l-2 border-accent-purple/30 pl-3 my-2">
                    {part.content}
                  </div>
                )
              default:
                return null
            }
          })}
        </div>

        {/* Metadata */}
        {!isUser && message.tokens && <MessageMeta message={message} />}
      </div>
    </div>
  )
}
