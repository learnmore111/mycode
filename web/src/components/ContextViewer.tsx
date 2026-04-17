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
  Zap,
  DollarSign,
  BarChart3,
  Layers,
  ArrowDown,
} from 'lucide-react'
import type { ContextSnapshot, ContextMessageInfo, CompactionEvent } from '../types'
import { getCompactionEvents } from '../api/compaction'

interface Props {
  snapshot: ContextSnapshot
  sessionId?: string
  onClose: () => void
}

/* ── Ring Gauge (circular progress) ── */
function RingGauge({ percent, size = 60, stroke = 5 }: { percent: number; size?: number; stroke?: number }) {
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (Math.min(100, percent) / 100) * circumference
  const color =
    percent < 50
      ? '#16A34A'
      : percent < 75
      ? '#CA8A04'
      : percent < 90
      ? '#EA580C'
      : '#DC2626'

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          className="text-surface-3"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-sm font-bold font-mono tabular-nums" style={{ color }}>
          {percent.toFixed(0)}%
        </span>
      </div>
    </div>
  )
}

/* ── Stat Card ── */
function StatCard({
  icon,
  label,
  value,
  sub,
  color = 'text-ink-secondary',
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div className="flex items-center gap-3 p-3 bg-surface-2 rounded-xl">
      <div className={`${color}`}>{icon}</div>
      <div className="flex-1 min-w-0">
        <div className="text-xxs text-ink-muted font-medium">{label}</div>
        <div className="text-sm font-semibold text-ink-strong font-mono tabular-nums">{value}</div>
      </div>
      {sub && <span className="text-xxs text-ink-faint font-mono">{sub}</span>}
    </div>
  )
}

/* ── Token Distribution Bar ── */
function TokenDistribution({
  system,
  tools,
  messages,
  total,
}: {
  system: number
  tools: number
  messages: number
  total: number
}) {
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0)

  const segments = [
    { label: '系统提示', tokens: system, color: 'bg-status-info', textColor: 'text-status-info' },
    { label: '工具定义', tokens: tools, color: 'bg-status-warning', textColor: 'text-status-warning' },
    { label: '消息内容', tokens: messages, color: 'bg-accent', textColor: 'text-accent' },
  ]

  return (
    <div>
      <div className="flex items-center gap-1.5 h-3 rounded-full overflow-hidden bg-surface-3">
        {segments.map((seg) => (
          <div
            key={seg.label}
            className={`h-full ${seg.color} transition-all duration-500 first:rounded-l-full last:rounded-r-full`}
            style={{ width: `${pct(seg.tokens)}%`, minWidth: seg.tokens > 0 ? '4px' : '0' }}
          />
        ))}
      </div>
      <div className="flex items-center gap-4 mt-2.5">
        {segments.map((seg) => (
          <div key={seg.label} className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${seg.color}`} />
            <span className="text-xxs text-ink-muted">{seg.label}</span>
            <span className={`text-xxs font-mono font-medium ${seg.textColor}`}>
              {seg.tokens.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── Section Accordion ── */
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
    <div className="border border-line rounded-xl overflow-hidden bg-surface-0">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2.5 w-full px-4 py-3 text-sm hover:bg-surface-hover transition-colors"
      >
        <div className={`transition-transform ${open ? 'rotate-90' : ''}`}>
          <ChevronRight size={12} className="text-ink-muted" />
        </div>
        <span className="text-ink-tertiary">{icon}</span>
        <span className="text-sm font-semibold text-ink-secondary">{title}</span>
        {badge}
        <span className="flex-1" />
        {tokens != null && (
          <span className="text-xs text-ink-muted font-mono tabular-nums">
            {tokens.toLocaleString()} <span className="text-ink-faint">tok</span>
          </span>
        )}
        {cacheStatus && (
          <span
            className={`text-xxs font-semibold px-2 py-0.5 rounded-full ${
              cacheStatus === 'cached'
                ? 'bg-status-success-light text-status-success'
                : 'bg-status-info-light text-status-info'
            }`}
          >
            {cacheStatus === 'cached' ? '✓ 已缓存' : '● 新'}
          </span>
        )}
      </button>
      {open && <div className="px-4 pb-4 border-t border-line-subtle">{children}</div>}
    </div>
  )
}

/* ── Message Item ── */
function MessageItem({ msg }: { msg: ContextMessageInfo }) {
  const [expanded, setExpanded] = useState(false)

  const roleConfig: Record<string, { color: string; bg: string; label: string }> = {
    user: { color: 'text-status-info', bg: 'bg-status-info-light', label: 'U' },
    assistant: { color: 'text-accent', bg: 'bg-accent-light', label: 'A' },
    tool: { color: 'text-status-warning', bg: 'bg-status-warning-light', label: 'T' },
    system: { color: 'text-ink-tertiary', bg: 'bg-surface-2', label: 'S' },
  }
  const rc = roleConfig[msg.role] || roleConfig.system

  const badges: React.ReactNode[] = []
  if (msg.is_compaction_summary)
    badges.push(
      <span key="c" className="text-xxs font-semibold px-1.5 py-0.5 rounded-full bg-accent-light text-accent">
        摘要
      </span>
    )
  if (msg.is_system_reminder)
    badges.push(
      <span key="r" className="text-xxs font-semibold px-1.5 py-0.5 rounded-full bg-status-info-light text-status-info">
        系统
      </span>
    )
  if (msg.tool_calls?.length)
    badges.push(
      <span key="t" className="text-xxs font-semibold px-1.5 py-0.5 rounded-full bg-status-warning-light text-status-warning">
        {msg.tool_calls.length} 调用
      </span>
    )

  return (
    <div className="border-b border-line-subtle last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2.5 text-xs hover:bg-surface-hover transition-colors"
      >
        <div className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>
          <ChevronRight size={10} className="text-ink-muted" />
        </div>
        <div className={`w-5 h-5 rounded-md ${rc.bg} flex items-center justify-center flex-shrink-0`}>
          <span className={`text-xxs font-bold ${rc.color}`}>{rc.label}</span>
        </div>
        <span className="text-ink-faint font-mono text-xxs">#{msg.index}</span>
        {badges}
        <span className="flex-1" />
        <span className="text-xxs text-ink-muted font-mono tabular-nums">
          {msg.estimated_tokens.toLocaleString()} tok
        </span>
        <div
          className={`w-2 h-2 rounded-full ${
            msg.cache_status === 'cached' ? 'bg-status-success' : 'bg-status-info'
          }`}
          title={msg.cache_status === 'cached' ? '已缓存' : '新内容'}
        />
      </button>
      {expanded && msg.content && (
        <div className="px-3 pb-3">
          <pre className="text-xs bg-surface-2 rounded-xl p-3.5 border border-line overflow-auto max-h-64 whitespace-pre-wrap text-ink-secondary leading-relaxed font-mono">
            {msg.content_truncated
              ? msg.content + `\n\n... (共 ${msg.full_length?.toLocaleString()} 字符)`
              : msg.content}
          </pre>
          {msg.tool_calls?.map((tc) => (
            <div key={tc.id} className="mt-2 flex items-center gap-2 text-xxs text-ink-muted font-mono">
              <Wrench size={10} className="text-status-warning" />
              <span className="font-medium text-ink-secondary">{tc.tool}</span>
              <span className="text-ink-faint truncate">({tc.args_preview})</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Compaction Event Item ── */
function CompactionEventItem({ event: evt }: { event: CompactionEvent }) {
  const [expanded, setExpanded] = useState(false)
  const time = new Date(evt.time_created).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <div className="border border-line rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3.5 py-2.5 text-xs hover:bg-surface-hover transition-colors"
      >
        <div className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>
          <ChevronRight size={11} className="text-ink-muted" />
        </div>
        <div className="w-6 h-6 rounded-lg bg-accent-light flex items-center justify-center">
          <span className="text-xxs font-bold text-accent font-mono">#{evt.iteration}</span>
        </div>
        <span className="text-ink-muted font-mono text-xxs">{time}</span>
        <span className="flex-1" />
        <div className="flex items-center gap-1.5">
          <ArrowDown size={10} className="text-status-error" />
          <span className="text-xxs font-semibold px-2 py-0.5 rounded-full bg-status-error-light text-status-error">
            -{evt.removed_turn_count} 轮 · -{evt.old_message_tokens.toLocaleString()} tok
          </span>
        </div>
      </button>
      {expanded && (
        <div className="px-3.5 pb-3.5 border-t border-line-subtle space-y-3">
          <div className="flex gap-4 mt-3 text-xs text-ink-muted font-mono">
            <span>移除: {evt.old_message_count} 条</span>
            <span>轮次: {evt.removed_turn_count}</span>
            <span>释放: {evt.old_message_tokens.toLocaleString()} tok</span>
          </div>

          <div>
            <div className="text-xs font-semibold text-accent mb-2 flex items-center gap-1.5">
              <Layers size={11} />
              压缩摘要
            </div>
            <pre className="text-xs bg-accent-light rounded-xl p-3.5 border border-accent/10 overflow-auto max-h-48 whitespace-pre-wrap text-ink-secondary leading-relaxed font-mono">
              {evt.summary}
            </pre>
          </div>

          <div>
            <div className="text-xs font-semibold text-status-error mb-2 flex items-center gap-1.5">
              <Archive size={11} />
              压缩的消息 ({evt.old_messages.length})
            </div>
            <div className="border border-line rounded-xl overflow-hidden max-h-96 overflow-y-auto">
              {evt.old_messages.map((msg, i) => {
                const roleConfig: Record<string, { color: string; bg: string; label: string }> = {
                  user: { color: 'text-status-info', bg: 'bg-status-info-light', label: 'U' },
                  assistant: { color: 'text-accent', bg: 'bg-accent-light', label: 'A' },
                  tool: { color: 'text-status-warning', bg: 'bg-status-warning-light', label: 'T' },
                  system: { color: 'text-ink-tertiary', bg: 'bg-surface-2', label: 'S' },
                }
                const rc = roleConfig[msg.role] || roleConfig.system
                return <OldMessageItem key={i} index={i} msg={msg} rc={rc} />
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function OldMessageItem({
  index,
  msg,
  rc,
}: {
  index: number
  msg: { role: string; content?: string }
  rc: { color: string; bg: string; label: string }
}) {
  const [expanded, setExpanded] = useState(false)
  const preview = (msg.content || '').slice(0, 100)

  return (
    <div className="border-b border-line-subtle last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-surface-hover transition-colors"
      >
        <div className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>
          <ChevronRight size={10} className="text-ink-muted" />
        </div>
        <div className={`w-4 h-4 rounded-md ${rc.bg} flex items-center justify-center flex-shrink-0`}>
          <span className={`text-xxs font-bold ${rc.color}`} style={{ fontSize: '8px' }}>{rc.label}</span>
        </div>
        <span className="text-ink-faint font-mono text-xxs">#{index}</span>
        <span className="flex-1" />
        <span className="text-xxs text-ink-muted truncate max-w-[200px] font-mono">{preview}</span>
      </button>
      {expanded && msg.content && (
        <div className="px-3 pb-2.5">
          <pre className="text-xs bg-surface-2 rounded-xl p-3 border border-line overflow-auto max-h-64 whitespace-pre-wrap text-ink-secondary leading-relaxed font-mono">
            {msg.content}
          </pre>
        </div>
      )}
    </div>
  )
}

/* ── Main Panel ── */
export default function ContextViewer({ snapshot, sessionId, onClose }: Props) {
  const { system, tools, messages, summary, actual_usage, iteration, model } = snapshot
  const [compactionEvents, setCompactionEvents] = useState<CompactionEvent[]>([])
  const [loadingCompaction, setLoadingCompaction] = useState(false)

  useEffect(() => {
    if (!sessionId) return
    setLoadingCompaction(true)
    getCompactionEvents(sessionId)
      .then(setCompactionEvents)
      .catch(() => setCompactionEvents([]))
      .finally(() => setLoadingCompaction(false))
  }, [sessionId])

  const msgTokens = messages.reduce((s, m) => s + (m.estimated_tokens || 0), 0)

  return (
    <div className="fixed inset-0 z-50 flex animate-fade-in">
      {/* Backdrop */}
      <div className="flex-1 bg-black/10 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="w-[520px] max-w-[92vw] h-full bg-surface-1 border-l border-line flex flex-col shadow-overlay animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-line bg-surface-0">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-status-info to-accent flex items-center justify-center shadow-xs">
            <Database size={14} className="text-white" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-ink-strong">上下文窗口</div>
            <div className="text-xxs text-ink-muted font-mono flex items-center gap-2">
              <span>迭代 #{iteration}</span>
              <span className="text-ink-faint">·</span>
              <span className="truncate max-w-[200px]">{model}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Overview Cards */}
        <div className="px-5 py-4 border-b border-line bg-surface-0 space-y-4">
          {/* Ring + Stats */}
          <div className="flex items-center gap-5">
            <RingGauge percent={summary.usage_percent} size={72} stroke={6} />
            <div className="flex-1 grid grid-cols-2 gap-2">
              <StatCard
                icon={<BarChart3 size={13} />}
                label="总 Token"
                value={summary.total_estimated_tokens.toLocaleString()}
                color="text-accent"
              />
              <StatCard
                icon={<Database size={13} />}
                label="上限"
                value={summary.context_limit.toLocaleString()}
                color="text-ink-tertiary"
              />
              {actual_usage ? (
                <>
                  <StatCard
                    icon={<Zap size={13} />}
                    label="缓存命中"
                    value={actual_usage.cache_read_tokens.toLocaleString()}
                    color="text-status-success"
                  />
                  <StatCard
                    icon={<DollarSign size={13} />}
                    label="本轮费用"
                    value={`$${actual_usage.total_cost.toFixed(4)}`}
                    color="text-status-warning"
                  />
                </>
              ) : (
                <>
                  <StatCard
                    icon={<Zap size={13} />}
                    label="已缓存"
                    value={summary.cached_estimated_tokens.toLocaleString()}
                    color="text-status-success"
                  />
                  <StatCard
                    icon={<Layers size={13} />}
                    label="新内容"
                    value={summary.new_estimated_tokens.toLocaleString()}
                    color="text-status-info"
                  />
                </>
              )}
            </div>
          </div>

          {/* Token distribution bar */}
          <TokenDistribution
            system={system.estimated_tokens}
            tools={tools.estimated_tokens}
            messages={msgTokens}
            total={summary.total_estimated_tokens}
          />
        </div>

        {/* Content sections */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {/* Compaction history */}
          {compactionEvents.length > 0 && (
            <Section
              title={`压缩历史 (${compactionEvents.length})`}
              icon={<Archive size={13} />}
              tokens={compactionEvents.reduce((s, e) => s + e.old_message_tokens, 0)}
              badge={
                <span className="text-xxs font-semibold px-2 py-0.5 rounded-full bg-accent-light text-accent">
                  {compactionEvents.reduce((s, e) => s + e.removed_turn_count, 0)} 轮已压缩
                </span>
              }
            >
              <div className="mt-3 space-y-2">
                {compactionEvents.map((evt) => (
                  <CompactionEventItem key={evt.id} event={evt} />
                ))}
              </div>
            </Section>
          )}
          {loadingCompaction && (
            <div className="text-xs text-ink-muted text-center py-4 flex items-center justify-center gap-2">
              <div className="w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />
              加载压缩历史...
            </div>
          )}

          {/* System prompt */}
          <Section
            title="系统提示词"
            icon={<FileText size={13} />}
            tokens={system.estimated_tokens}
            cacheStatus={system.cache_status}
          >
            {system.content && !system.content.startsWith('(') ? (
              <pre className="text-xs bg-surface-2 rounded-xl p-3.5 border border-line overflow-auto max-h-64 whitespace-pre-wrap text-ink-tertiary mt-3 leading-relaxed font-mono">
                {system.content.length > 2000
                  ? system.content.slice(0, 2000) + `\n\n... (共 ${system.content.length.toLocaleString()} 字符)`
                  : system.content}
              </pre>
            ) : (
              <div className="mt-3 flex items-center gap-2.5 px-4 py-3 bg-surface-2 rounded-xl border border-line-subtle text-xs text-ink-muted">
                <FileText size={13} className="text-ink-faint flex-shrink-0" />
                <span>系统提示词仅在实时对话中可查看（发送消息后自动获取）</span>
              </div>
            )}
          </Section>

          {/* Tools */}
          <Section
            title={`工具 (${tools.count})`}
            icon={<Wrench size={13} />}
            tokens={tools.estimated_tokens}
            cacheStatus={tools.cache_status}
          >
            {tools.names.length > 0 ? (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {tools.names.map((name) => (
                  <span
                    key={name}
                    className="text-xxs font-mono font-semibold px-2.5 py-1 rounded-lg bg-surface-2 text-ink-secondary border border-line hover:border-accent/30 hover:text-accent transition-colors cursor-default"
                  >
                    {name}
                  </span>
                ))}
              </div>
            ) : (
              <div className="mt-3 flex items-center gap-2.5 px-4 py-3 bg-surface-2 rounded-xl border border-line-subtle text-xs text-ink-muted">
                <Wrench size={13} className="text-ink-faint flex-shrink-0" />
                <span>工具列表仅在实时对话中可查看（发送消息后自动获取）</span>
              </div>
            )}
          </Section>

          {/* Messages */}
          <Section
            title={`消息 (${messages.length})`}
            icon={<MessageSquare size={13} />}
            tokens={msgTokens}
            defaultOpen
          >
            {messages.length > 0 ? (
              <div className="mt-3 border border-line rounded-xl overflow-hidden">
                {messages.map((msg) => (
                  <MessageItem key={msg.index} msg={msg} />
                ))}
              </div>
            ) : (
              <div className="mt-3 flex items-center gap-2.5 px-4 py-3 bg-surface-2 rounded-xl border border-line-subtle text-xs text-ink-muted">
                <MessageSquare size={13} className="text-ink-faint flex-shrink-0" />
                <span>暂无消息记录</span>
              </div>
            )}
          </Section>
        </div>
      </div>
    </div>
  )
}
