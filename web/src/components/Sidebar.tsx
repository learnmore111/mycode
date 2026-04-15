import { Plus, Trash2, MessageSquare, Loader2, PanelLeftClose, PanelLeft } from 'lucide-react'
import { useState } from 'react'
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
  const [collapsed, setCollapsed] = useState(false)

  if (collapsed) {
    return (
      <div className="flex flex-col items-center py-4 px-1.5 gap-2">
        <button
          onClick={() => setCollapsed(false)}
          className="p-2 rounded-lg hover:bg-white/10 text-white/50 hover:text-white/80 transition-colors"
          title="Expand sidebar"
        >
          <PanelLeft size={18} />
        </button>
        <button
          onClick={onCreate}
          className="p-2 rounded-lg hover:bg-white/10 text-white/50 hover:text-white/80 transition-colors"
          title="New session"
        >
          <Plus size={18} />
        </button>
      </div>
    )
  }

  return (
    <aside className="w-60 flex-shrink-0 flex flex-col border-r border-white/5 bg-black/20">
      {/* Header */}
      <div className="p-3 flex items-center justify-between">
        <h1 className="text-sm font-semibold text-white/80 tracking-wide">MyCode</h1>
        <div className="flex items-center gap-1">
          <button
            onClick={onCreate}
            className="p-1.5 rounded-lg hover:bg-white/10 text-white/40 hover:text-white/80 transition-colors"
            title="New session"
          >
            <Plus size={16} />
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="p-1.5 rounded-lg hover:bg-white/10 text-white/40 hover:text-white/80 transition-colors"
            title="Collapse"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={18} className="animate-spin text-white/30" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-8 text-white/30 text-xs">
            还没有会话
            <br />
            点击 + 开始
          </div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`group flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-all ${
                s.id === activeId
                  ? 'bg-white/10 text-white'
                  : 'text-white/50 hover:bg-white/5 hover:text-white/75'
              }`}
            >
              <MessageSquare size={13} className="flex-shrink-0 opacity-60" />
              <span className="flex-1 truncate text-xs">{s.title || '未命名会话'}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(s.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/10 text-white/30 hover:text-red-400 transition-all"
                title="删除"
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
