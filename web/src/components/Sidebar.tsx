import { Plus, Trash2, MessageSquare, Loader2, PanelLeftClose, PanelLeft, Search } from 'lucide-react'
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
      <div className="flex flex-col items-center py-4 px-1.5 gap-2 bg-surface-0 border-r border-border-subtle">
        <button
          onClick={() => setCollapsed(false)}
          className="p-2 rounded-md hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
          title="展开侧边栏"
        >
          <PanelLeft size={18} />
        </button>
        <button
          onClick={onCreate}
          className="p-2 rounded-md hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
          title="新建会话"
        >
          <Plus size={18} />
        </button>
      </div>
    )
  }

  return (
    <aside className="w-64 flex-shrink-0 flex flex-col border-r border-border-subtle bg-surface-0">
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center">
            <span className="text-white text-xs font-bold">M</span>
          </div>
          <h1 className="text-sm font-semibold text-text-primary tracking-tight">MyCode</h1>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={onCreate}
            className="p-1.5 rounded-md hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
            title="新建会话"
          >
            <Plus size={15} />
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="p-1.5 rounded-md hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
            title="折叠侧边栏"
          >
            <PanelLeftClose size={15} />
          </button>
        </div>
      </div>

      {/* Section label */}
      <div className="px-4 pt-2 pb-1">
        <span className="text-[11px] font-medium text-text-muted uppercase tracking-wider">Sessions</span>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 size={16} className="animate-spin text-text-muted" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-8 text-text-muted text-xs">
            <MessageSquare size={14} />
            <span>点击 + 开始新会话</span>
          </div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => onSelect(s.id)}
              className={`group flex items-center gap-2 px-2 py-2 rounded-md cursor-pointer transition-all relative ${
                s.id === activeId
                  ? 'bg-surface-2 text-text-primary'
                  : 'text-text-secondary hover:bg-surface-1 hover:text-text-primary'
              }`}
            >
              {/* Active indicator bar */}
              {s.id === activeId && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-accent-blue rounded-r" />
              )}
              <MessageSquare size={13} className="flex-shrink-0 opacity-60" />
              <span className="flex-1 truncate text-xs">{s.title || '未命名会话'}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(s.id)
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-surface-3 text-text-muted hover:text-accent-red transition-all"
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
