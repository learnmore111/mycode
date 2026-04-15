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
    <div className="flex items-center gap-3 px-4 py-3 border-b border-white/5 bg-black/10 backdrop-blur-sm">
      <h2 className="text-sm font-medium text-white/70 truncate flex-1">
        {session.title || '未命名会话'}
      </h2>

      {/* Context viewer button */}
      {contextSnapshot && (
        <button
          onClick={onViewContext}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-white/45 hover:text-white/70 hover:bg-white/8 transition-all"
          title="查看完整上下文"
        >
          <Database size={13} />
          <span>{contextSnapshot.summary.usage_percent.toFixed(0)}%</span>
          <div className="w-12 h-1.5 rounded-full bg-white/5 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                contextSnapshot.summary.usage_percent < 50
                  ? 'bg-green-400'
                  : contextSnapshot.summary.usage_percent < 75
                  ? 'bg-yellow-400'
                  : 'bg-red-400'
              }`}
              style={{ width: `${Math.min(100, contextSnapshot.summary.usage_percent)}%` }}
            />
          </div>
        </button>
      )}
    </div>
  )
}
