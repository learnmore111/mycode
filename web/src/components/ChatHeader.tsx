import { Database } from 'lucide-react'
import type { Session, AgentInfo, ContextSnapshot } from '../types'

interface Props {
  session: Session
  models: { id: string; name: string; provider: string }[]
  agents: AgentInfo[]
  selectedModel?: string
  selectedAgent?: string
  onModelChange: (m: string | undefined) => void
  onAgentChange: (a: string | undefined) => void
  contextSnapshot?: ContextSnapshot | null
  onViewContext?: () => void
}

export default function ChatHeader({ session, contextSnapshot, onViewContext }: Props) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-border-subtle bg-surface-0">
      <h2 className="text-sm font-medium text-text-primary truncate flex-1">
        {session.title || '未命名会话'}
      </h2>

      {/* Context viewer button */}
      {contextSnapshot && (
        <button
          onClick={onViewContext}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs text-text-secondary hover:text-text-primary hover:bg-surface-2 transition-all"
          title="查看完整上下文"
        >
          <Database size={13} />
          <span>{contextSnapshot.summary.usage_percent.toFixed(0)}%</span>
          <div className="w-12 h-1.5 rounded-full bg-surface-2 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                contextSnapshot.summary.usage_percent < 50
                  ? 'bg-accent-green'
                  : contextSnapshot.summary.usage_percent < 75
                  ? 'bg-accent-amber'
                  : 'bg-accent-red'
              }`}
              style={{ width: `${Math.min(100, contextSnapshot.summary.usage_percent)}%` }}
            />
          </div>
        </button>
      )}
    </div>
  )
}
