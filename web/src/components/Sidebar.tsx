import { Plus, Trash2, MessageSquare, Loader2 } from 'lucide-react'
import type { Session } from '../types'

interface Props {
  sessions: Session[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  loading: boolean
}

export default function Sidebar({ sessions, activeId, onSelect, onCreate, onDelete, loading }: Props) {
  return (
    <aside className="w-64 flex-shrink-0 border-r border-gray-800 bg-gray-950 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-800 flex items-center justify-between">
        <h1 className="text-lg font-bold text-blue-400">OpenCode</h1>
        <button
          onClick={onCreate}
          className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
          title="New session"
        >
          <Plus size={18} />
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={20} className="animate-spin text-gray-500" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-8 text-gray-500 text-sm">
            No sessions yet.
            <br />
            Click + to start.
          </div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
                s.id === activeId
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-400 hover:bg-gray-900 hover:text-gray-200'
              }`}
            >
              <MessageSquare size={14} className="flex-shrink-0" />
              <span className="flex-1 truncate text-sm">{s.title || 'Untitled'}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(s.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-gray-700 text-gray-500 hover:text-red-400 transition-all"
                title="Delete"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
