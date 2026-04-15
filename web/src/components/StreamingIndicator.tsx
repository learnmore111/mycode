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
    <div className="flex gap-3 justify-start">
      <div className="flex-shrink-0 w-7 h-7 rounded-full bg-white/10 flex items-center justify-center mt-1">
        <Bot size={14} className="text-blue-300" />
      </div>
      <div className="max-w-[80%]">
        <div className="rounded-2xl rounded-bl-md px-4 py-2.5 bg-white/8 text-gray-100 border border-white/8">
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
            <div className="flex items-center gap-2 text-white/40">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400/60 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400/60 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400/60 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span className="text-xs">思考中...</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
