import { Bot } from 'lucide-react'
import type { StreamingPart } from '../types'
import TextContent from './TextContent'
import ToolExecution from './ToolExecution'

interface Props {
  text: string
  parts: StreamingPart[]
}

export default function StreamingIndicator({ text, parts }: Props) {
  return (
    <div className="flex gap-3">
      {/* Avatar */}
      <div className="flex-shrink-0 mt-0.5">
        <div className="w-7 h-7 rounded-md bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center">
          <Bot size={14} className="text-white" />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 max-w-3xl">
        <div className="text-xs font-medium text-text-tertiary mb-1">AI 助手</div>

        <div className="text-sm text-text-primary">
          {text && <TextContent content={text} />}

          {parts.map((part) => (
            <ToolExecution
              key={part.id}
              part={{
                id: part.id,
                type: part.type,
                content: part.content,
                tool: part.tool,
                toolCallId: part.toolCallId,
                state: part.state,
                time: { created: Date.now() },
              }}
            />
          ))}

          {!text && parts.length === 0 && (
            <div className="flex items-center gap-2 text-text-muted">
              <div className="dot-pulse flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-accent-purple" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-accent-purple" style={{ animationDelay: '200ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-accent-purple" style={{ animationDelay: '400ms' }} />
              </div>
              <span className="text-xs">思考中...</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
