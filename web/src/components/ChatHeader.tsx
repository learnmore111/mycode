import type { Session, AgentInfo } from '../types'

interface Props {
  session: Session
  models: { id: string; name: string; provider: string }[]
  agents: AgentInfo[]
  selectedModel?: string
  selectedAgent?: string
  onModelChange: (m: string | undefined) => void
  onAgentChange: (a: string | undefined) => void
}

export default function ChatHeader({ session }: Props) {
  return (
    <div className="flex items-center gap-4 px-4 py-3 border-b border-white/5 bg-black/10 backdrop-blur-sm">
      <h2 className="text-sm font-medium text-white/70 truncate flex-1">
        {session.title || '未命名会话'}
      </h2>
    </div>
  )
}
