import { useState, useEffect } from 'react'
import {
  X,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Wrench,
  FileText,
  Database,
  Archive,
} from 'lucide-react'
import type { ContextSnapshot, ContextMessageInfo, CompactionEvent } from '../types'
import { getCompactionEvents } from '../api/compaction'

interface Props {
  snapshot: ContextSnapshot
  sessionId?: string
  onClose: () => void
}

function TokenBar({ used, limit, label }: { used: number; limit: number; label?: string }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0
  const color = pct < 50 ? 'bg-accent-green' : pct < 75 ? 'bg-accent-amber' : pct < 90 ? 'bg-orange-400' : 'bg-accent-red'
  return (
    <div>
      {label && <div className="text-[10px] text-text-muted mb-1">{label}</div>}
      <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[10px] text-text-muted mt-0.5">
        {used.toLocaleString()} / {limit.toLocaleString()} tokens ({pct.toFixed(1)}%)
      </div>
    </div>
  )
}

function Section({
  title,
  icon,
  tokens,
  cacheStatus,
  defaultOpen = false,
  badge,
  children,
}: {
  title: string
  icon: React.ReactNode
  tokens?: number
  cacheStatus?: string
  defaultOpen?: boolean
  badge?: React.ReactNode
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-border-subtle rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-3 py-2.5 text-xs hover:bg-surface-2 transition-colors"
      >
        {open ? <ChevronDown size={12} className="text-text-muted" /> : <ChevronRight size={12} className="text-text-muted" />}
        <span className="text-text-tertiary">{icon}</span>
        <span className="font-medium text-text-secondary">{title}</span>
        {badge}
        <span className="flex-1" />
        {tokens != null && (
          <span className="text-[10px] text-text-muted font-mono">{tokens.toLocaleString()} tok</span>
        )}
        {cacheStatus && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded ${
              cacheStatus === 'cached' ? 'bg-accent-green/15 text-accent-green' : 'bg-accent-blue/15 text-accent-blue'
            }`}
          >
            {cacheStatus === 'cached' ? '已缓存' : '新内容'}
          </span>
        )}
      </button>
      {open && <div className="px-3 pb-3 border-t border-border-subtle">{children}</div>}
    </div>
  )
}

function MessageItem({ msg }: { msg: ContextMessageInfo }) {
  const [expanded, setExpanded] = useState(false)
  const roleIcon =
    msg.role === 'user' ? '👤' : msg.role === 'assistant' ? '🤖' : msg.role === 'tool' ? '🔧' : '📋'
  const roleColor =
    msg.role === 'user'
      ? 'text-accent-blue'
      : msg.role === 'assistant'
      ? 'text-accent-green'
      : msg.role === 'tool'
      ? 'text-accent-amber'
      : 'text-text-tertiary'

  const badges: React.ReactNode[] = []
  if (msg.is_compaction_summary) badges.push(<span key="c" className="text-[10px] px-1 py-0.5 rounded bg-accent-purple/15 text-accent-purple">压缩摘要</span>)
  if (msg.is_system_reminder) badges.push(<span key="r" className="text-[10px] px-1 py-0.5 rounded bg-cyan-500/15 text-cyan-400">系统提醒</span>)
  if (msg.tool_calls?.length) badges.push(<span key="t" className="text-[10px] px-1 py-0.5 rounded bg-orange-500/15 text-orange-400">{msg.tool_calls.length} 工具调用</span>)

  return (
    <div className="border-b border-border-subtle last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-2 py-1.5 text-xs hover:bg-surface-2 transition-colors"
      >
        {expanded ? <ChevronDown size={10} className="text-text-muted" /> : <ChevronRight size={10} className="text-text-muted" />}
        <span>{roleIcon}</span>
        <span className={`font-mono ${roleColor}`}>{msg.role}</span>
        <span className="text-text-muted">#{msg.index}</span>
        {badges}
        <span className="flex-1" />
        <span className="text-[10px] text-text-muted font-mono">{msg.estimated_tokens} tok</span>
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            msg.cache_status === 'cached' ? 'bg-accent-green' : 'bg-accent-blue'
          }`}
        />
      </button>
      {expanded && msg.content && (
        <div className="px-2 pb-2">
          <pre className="text-[11px] bg-[#0d0f14] rounded-lg p-2 border border-border-subtle overflow-auto max-h-64 whitespace-pre-wrap text-text-secondary leading-relaxed font-mono">
            {msg.content_truncated ? msg.content + `\n\n... (${msg.full_length} 字符)` : msg.content}
          </pre>
          {msg.tool_calls?.map((tc) => (
            <div key={tc.id} className="mt-1 text-[10px] text-text-muted font-mono">
              → {tc.tool}({tc.args_preview})
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CompactionEventItem({ event: evt }: { event: CompactionEvent }) {
  const [expanded, setExpanded] = useState(false)
  const time = new Date(evt.time_created).toLocaleTimeString()

  return (
    <div className="border border-border-subtle rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-surface-2 transition-colors"
      >
        {expanded ? <ChevronDown size={10} className="text-text-muted" /> : <ChevronRight size={10} className="text-text-muted" />}
        <span className="text-accent-purple">迭代 #{evt.iteration}</span>
        <span className="text-text-muted">{time}</span>
        <span className="flex-1" />
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-red/15 text-accent-red">
          -{evt.removed_turn_count} 轮 / -{evt.old_message_tokens.toLocaleString()} tok
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-border-subtle space-y-3">
          {/* Metrics */}
          <div className="flex gap-4 mt-2 text-[10px] text-text-muted">
            <span>移除消息: {evt.old_message_count}</span>
            <span>移除轮次: {evt.removed_turn_count}</span>
            <span>释放 tokens: {evt.old_message_tokens.toLocaleString()}</span>
            <span>摘要长度: {evt.summary_length}</span>
          </div>

          {/* Summary */}
          <div>
            <div className="text-[10px] text-accent-purple font-medium mb-1">生成的摘要</div>
            <pre className="text-[11px] bg-[#0d0f14] rounded-lg p-2 border border-accent-purple/20 overflow-auto max-h-48 whitespace-pre-wrap text-text-secondary leading-relaxed font-mono">
              {evt.summary}
            </pre>
          </div>

          {/* Original messages */}
          <div>
            <div className="text-[10px] text-accent-red font-medium mb-1">
              被压缩的原始消息 ({evt.old_messages.length})
            </div>
            <div className="border border-border-subtle rounded-lg overflow-hidden max-h-96 overflow-y-auto">
              {evt.old_messages.map((msg, i) => {
                const roleIcon = msg.role === 'user' ? '👤' : msg.role === 'assistant' ? '🤖' : msg.role === 'tool' ? '🔧' : '📋'
                const roleColor =
                  msg.role === 'user' ? 'text-accent-blue'
                  : msg.role === 'assistant' ? 'text-accent-green'
                  : msg.role === 'tool' ? 'text-accent-amber'
                  : 'text-text-tertiary'
                return (
                  <OldMessageItem key={i} index={i} msg={msg} roleIcon={roleIcon} roleColor={roleColor} />
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function OldMessageItem({ index, msg, roleIcon, roleColor }: {
  index: number
  msg: { role: string; content?: string }
  roleIcon: string
  roleColor: string
}) {
  const [expanded, setExpanded] = useState(false)
  const preview = (msg.content || '').slice(0, 80)

  return (
    <div className="border-b border-border-subtle last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-2 py-1.5 text-xs hover:bg-surface-2 transition-colors"
      >
        {expanded ? <ChevronDown size={10} className="text-text-muted" /> : <ChevronRight size={10} className="text-text-muted" />}
        <span>{roleIcon}</span>
        <span className={`font-mono ${roleColor}`}>{msg.role}</span>
        <span className="text-text-muted">#{index}</span>
        <span className="flex-1" />
        <span className="text-[10px] text-text-muted truncate max-w-[200px]">{preview}</span>
      </button>
      {expanded && msg.content && (
        <div className="px-2 pb-2">
          <pre className="text-[11px] bg-[#0d0f14] rounded-lg p-2 border border-border-subtle overflow-auto max-h-64 whitespace-pre-wrap text-text-secondary leading-relaxed font-mono">
            {msg.content}
          </pre>
        </div>
      )}
    </div>
  )
}

export default function ContextViewer({ snapshot, sessionId, onClose }: Props) {
  const { system, tools, messages, summary, actual_usage, iteration, model } = snapshot
  const [compactionEvents, setCompactionEvents] = useState<CompactionEvent[]>([])
  const [loadingCompaction, setLoadingCompaction] = useState(false)

  // Load compaction events when viewer opens
  useEffect(() => {
    if (!sessionId) return
    setLoadingCompaction(true)
    getCompactionEvents(sessionId)
      .then(setCompactionEvents)
      .catch(() => setCompactionEvents([]))
      .finally(() => setLoadingCompaction(false))
  }, [sessionId])

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div className="flex-1" onClick={onClose} />

      {/* Panel */}
      <div className="w-[520px] max-w-[90vw] h-full bg-surface-0 border-l border-border flex flex-col shadow-modal">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border-subtle">
          <Database size={16} className="text-accent-blue" />
          <div className="flex-1">
            <div className="text-sm font-medium text-text-primary">上下文查看器</div>
            <div className="text-[10px] text-text-muted">
              迭代 #{iteration} · {model}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Token usage bar */}
        <div className="px-4 py-3 border-b border-border-subtle">
          <TokenBar used={summary.total_estimated_tokens} limit={summary.context_limit} label="上下文窗口用量" />
          <div className="flex gap-4 mt-2 text-[10px]">
            {actual_usage ? (
              <>
                {actual_usage.cache_read_tokens > 0 && (
                  <div className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-accent-green" />
                    <span className="text-text-muted">缓存命中 {actual_usage.cache_read_tokens.toLocaleString()}</span>
                  </div>
                )}
                <div className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-accent-blue" />
                  <span className="text-text-muted">输入 {actual_usage.input_tokens.toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-accent-purple" />
                  <span className="text-text-muted">输出 {actual_usage.output_tokens.toLocaleString()}</span>
                </div>
                {actual_usage.total_cost > 0 && (
                  <div className="flex items-center gap-1">
                    <span className="text-text-muted">${actual_usage.total_cost.toFixed(4)}</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-accent-green" />
                  <span className="text-text-muted">已缓存 {summary.cached_estimated_tokens.toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-accent-blue" />
                  <span className="text-text-muted">新内容 {summary.new_estimated_tokens.toLocaleString()}</span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {/* Compaction History — show at top if there are events */}
          {compactionEvents.length > 0 && (
            <Section
              title={`压缩历史 (${compactionEvents.length})`}
              icon={<Archive size={13} />}
              tokens={compactionEvents.reduce((s, e) => s + e.old_message_tokens, 0)}
              badge={
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-purple/15 text-accent-purple">
                  共 {compactionEvents.reduce((s, e) => s + e.removed_turn_count, 0)} 轮被压缩
                </span>
              }
            >
              <div className="mt-2 space-y-2">
                {compactionEvents.map((evt) => (
                  <CompactionEventItem key={evt.id} event={evt} />
                ))}
              </div>
            </Section>
          )}
          {loadingCompaction && (
            <div className="text-[10px] text-text-muted text-center py-2">加载压缩历史...</div>
          )}

          {/* System Prompt */}
          <Section
            title="System Prompt"
            icon={<FileText size={13} />}
            tokens={system.estimated_tokens}
            cacheStatus={system.cache_status}
          >
            <pre className="text-[11px] bg-[#0d0f14] rounded-lg p-2 border border-border-subtle overflow-auto max-h-64 whitespace-pre-wrap text-text-tertiary mt-2 leading-relaxed font-mono">
              {system.content.length > 2000
                ? system.content.slice(0, 2000) + `\n\n... (${system.content.length} 字符)`
                : system.content}
            </pre>
          </Section>

          {/* Tools */}
          <Section
            title={`工具 (${tools.count})`}
            icon={<Wrench size={13} />}
            tokens={tools.estimated_tokens}
            cacheStatus={tools.cache_status}
          >
            <div className="flex flex-wrap gap-1 mt-2">
              {tools.names.map((name) => (
                <span key={name} className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-text-tertiary font-mono border border-border-subtle">
                  {name}
                </span>
              ))}
            </div>
          </Section>

          {/* Messages */}
          <Section
            title={`消息 (${messages.length})`}
            icon={<MessageSquare size={13} />}
            tokens={messages.reduce((s, m) => s + (m.estimated_tokens || 0), 0)}
            defaultOpen
          >
            <div className="mt-2 border border-border-subtle rounded-lg overflow-hidden">
              {messages.map((msg) => (
                <MessageItem key={msg.index} msg={msg} />
              ))}
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
