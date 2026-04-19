import { useState, useRef, useEffect } from 'react'
import {
  Database,
  ChevronDown,
  Cpu,
  Bot,
  Check,
  Zap,
  Clock,
  Search,
  PauseCircle,
} from 'lucide-react'
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
  isPaused?: boolean
}

function Dropdown<T extends { id: string; label: string; sub?: string }>({
  items,
  value,
  onChange,
  placeholder,
  icon,
  searchable = false,
}: {
  items: T[]
  value?: string
  onChange: (v: string | undefined) => void
  placeholder: string
  icon: React.ReactNode
  searchable?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    if (open && searchable) {
      setTimeout(() => searchRef.current?.focus(), 50)
    }
    if (!open) setQuery('')
  }, [open, searchable])

  const selected = items.find((i) => i.id === value)
  const filtered = query
    ? items.filter(
        (i) =>
          i.label.toLowerCase().includes(query.toLowerCase()) ||
          i.sub?.toLowerCase().includes(query.toLowerCase()),
      )
    : items

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs transition-all border ${
          open
            ? 'border-accent/30 bg-accent-light text-accent shadow-xs'
            : 'border-transparent hover:bg-surface-hover text-ink-secondary hover:text-ink'
        }`}
      >
        {icon}
        <span className="font-medium truncate max-w-[140px]">
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown
          size={11}
          className={`transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1.5 min-w-[220px] max-w-[320px] bg-surface-0 border border-line rounded-xl shadow-lg z-50 overflow-hidden animate-slide-up">
          {searchable && (
            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-line-subtle">
              <Search size={12} className="text-ink-muted flex-shrink-0" />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索..."
                className="flex-1 bg-transparent text-xs text-ink placeholder:text-ink-muted outline-none"
              />
            </div>
          )}

          <div className="max-h-64 overflow-y-auto py-1">
            <button
              onClick={() => {
                onChange(undefined)
                setOpen(false)
              }}
              className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs hover:bg-surface-hover transition-colors ${
                !value ? 'text-accent' : 'text-ink-muted'
              }`}
            >
              <span className="w-4 h-4 flex items-center justify-center">
                {!value && <Check size={12} />}
              </span>
              <span className="font-medium">默认</span>
            </button>

            {filtered.map((item) => (
              <button
                key={item.id}
                onClick={() => {
                  onChange(item.id)
                  setOpen(false)
                }}
                className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs hover:bg-surface-hover transition-colors ${
                  item.id === value ? 'text-accent bg-accent-light/50' : 'text-ink-secondary'
                }`}
              >
                <span className="w-4 h-4 flex items-center justify-center flex-shrink-0">
                  {item.id === value && <Check size={12} className="text-accent" />}
                </span>
                <div className="flex-1 min-w-0 text-left">
                  <div className="font-medium truncate">{item.label}</div>
                  {item.sub && (
                    <div className="text-xxs text-ink-muted truncate mt-0.5">{item.sub}</div>
                  )}
                </div>
              </button>
            ))}

            {filtered.length === 0 && (
              <div className="px-3 py-4 text-xs text-ink-muted text-center">无匹配结果</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ContextUsagePill({
  snapshot,
  onClick,
}: {
  snapshot: ContextSnapshot
  onClick?: () => void
}) {
  const pct = snapshot.summary.usage_percent
  const color =
    pct < 50
      ? 'text-status-success'
      : pct < 75
      ? 'text-status-warning'
      : 'text-status-error'
  const barColor =
    pct < 50
      ? 'bg-status-success'
      : pct < 75
      ? 'bg-status-warning'
      : 'bg-status-error'
  const bgColor =
    pct < 50
      ? 'bg-status-success-light'
      : pct < 75
      ? 'bg-status-warning-light'
      : 'bg-status-error-light'

  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-xs transition-all hover:shadow-xs ${bgColor} border border-transparent hover:border-line`}
      title="查看上下文详情"
    >
      <Database size={12} className={color} />
      <div className="flex items-center gap-2">
        <div className="w-16 h-1.5 rounded-full bg-black/[0.06] overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${Math.min(100, pct)}%` }}
          />
        </div>
        <span className={`font-mono font-semibold tabular-nums ${color}`}>
          {pct.toFixed(0)}%
        </span>
      </div>
      {snapshot.actual_usage && snapshot.actual_usage.total_cost > 0 && (
        <span className="text-ink-muted font-mono text-xxs">
          ${snapshot.actual_usage.total_cost.toFixed(3)}
        </span>
      )}
    </button>
  )
}

export default function ChatHeader({
  session,
  models,
  agents,
  selectedModel,
  selectedAgent,
  onModelChange,
  onAgentChange,
  contextSnapshot,
  onViewContext,
  isPaused = false,
}: Props) {
  const modelItems = models.map((m) => ({
    id: m.id,
    label: m.name.split(' / ').pop() || m.name,
    sub: m.provider,
  }))

  const agentItems = agents.map((a) => ({
    id: a.name,
    label: a.name,
    sub: a.description,
  }))

  const age = session.time.created
    ? formatTimeAgo(session.time.created)
    : null

  return (
    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line bg-surface-0/80 backdrop-blur-sm">
      <div className="flex items-center gap-2.5 min-w-0 flex-shrink">
        <h2 className="text-sm font-semibold text-ink-strong truncate max-w-[220px]">
          {session.title || '新会话'}
        </h2>
        {age && (
          <div className="flex items-center gap-1 text-xxs text-ink-muted flex-shrink-0">
            <Clock size={10} />
            <span>{age}</span>
          </div>
        )}
        {isPaused && (
          <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-status-warning-light text-status-warning text-xxs font-medium">
            <PauseCircle size={10} />
            <span>已暂停</span>
          </div>
        )}
      </div>

      <div className="w-px h-4 bg-line mx-1" />

      {models.length > 0 && (
        <Dropdown
          items={modelItems}
          value={selectedModel}
          onChange={onModelChange}
          placeholder="模型"
          icon={<Cpu size={12} />}
          searchable={models.length > 5}
        />
      )}

      {agents.length > 0 && (
        <Dropdown
          items={agentItems}
          value={selectedAgent}
          onChange={onAgentChange}
          placeholder="智能体"
          icon={<Bot size={12} />}
        />
      )}

      <div className="flex-1" />

      {contextSnapshot && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-surface-2 text-xxs text-ink-muted font-mono">
          <Zap size={10} />
          <span>迭代 #{contextSnapshot.iteration}</span>
        </div>
      )}

      {contextSnapshot && (
        <ContextUsagePill snapshot={contextSnapshot} onClick={onViewContext} />
      )}
    </div>
  )
}

function formatTimeAgo(timestamp: number): string {
  const now = Date.now()
  const ts = timestamp < 1e12 ? timestamp * 1000 : timestamp
  const diff = Math.floor((now - ts) / 1000)
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return `${Math.floor(diff / 86400)}天前`
}
