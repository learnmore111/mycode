import type { StreamingPart } from '../types'
import TextContent from './TextContent'
import ToolExecution from './ToolExecution'
import ReasoningBlock from './ReasoningBlock'

interface Props {
  text: string
  parts: StreamingPart[]
}

export default function StreamingIndicator({ text, parts }: Props) {
  const reasoningParts = parts.filter((part) => part.type === 'reasoning')
  const toolParts = parts.filter((part) => part.type === 'tool')

  return (
    <div className="animate-fade-in">
      {/* Role label */}
      <div className="flex items-center gap-2 mb-2">
        <div className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold bg-accent text-white">
          A
        </div>
        <span className="text-xs font-semibold text-ink-strong">助手</span>
        <div className="flex items-center gap-1.5 ml-1">
          <div className="w-1 h-1 rounded-full bg-accent animate-pulse-soft" />
          <span className="text-xxs text-ink-muted">正在回复</span>
        </div>
      </div>

      {/* Content */}
      <div className="pl-7 text-ink">
        {reasoningParts.map((part) => (
          <ReasoningBlock
            key={part.id}
            content={part.content}
            streaming
          />
        ))}

        {toolParts.map((part) => (
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

        {text ? (
          <div className="streaming-cursor">
            <TextContent content={text} />
          </div>
        ) : null}

        {!text && parts.length === 0 && (
          <div className="flex items-center gap-2.5 text-ink-muted text-sm py-1">
            <div className="flex gap-1">
              <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-soft" style={{ animationDelay: '0s' }} />
              <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-soft" style={{ animationDelay: '0.3s' }} />
              <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-soft" style={{ animationDelay: '0.6s' }} />
            </div>
            <span>思考中...</span>
          </div>
        )}
      </div>
    </div>
  )
}
