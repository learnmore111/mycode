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

export default function ChatHeader({
  session,
  models,
  agents,
  selectedModel,
  selectedAgent,
  onModelChange,
  onAgentChange,
}: Props) {
  return (
    <div className="flex items-center gap-4 px-4 py-3 border-b border-gray-800 bg-gray-950/80 backdrop-blur">
      <h2 className="text-sm font-medium text-gray-200 truncate flex-1">{session.title || 'Untitled Session'}</h2>

      {/* Model selector */}
      {models.length > 0 && (
        <select
          value={selectedModel ?? ''}
          onChange={(e) => onModelChange(e.target.value || undefined)}
          className="bg-gray-800 text-gray-300 text-xs rounded-md px-2 py-1 border border-gray-700 focus:outline-none focus:border-blue-500"
        >
          <option value="">Default Model</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      )}

      {/* Agent selector */}
      {agents.length > 0 && (
        <select
          value={selectedAgent ?? ''}
          onChange={(e) => onAgentChange(e.target.value || undefined)}
          className="bg-gray-800 text-gray-300 text-xs rounded-md px-2 py-1 border border-gray-700 focus:outline-none focus:border-blue-500"
        >
          <option value="">Default Agent</option>
          {agents.map((a) => (
            <option key={a.name} value={a.name}>
              {a.name} — {a.description}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
