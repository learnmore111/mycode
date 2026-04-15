import { useState } from 'react'
import {
  X,
  ChevronDown,
  ChevronRight,
  Cpu,
  MessageSquare,
  Wrench,
  FileText,
  Zap,
  Database,
} from 'lucide-react'
import type { ContextSnapshot, ContextMessageInfo } from '../types'

interface Props {
  snapshot: ContextSnapshot
  onClose: () => void
}

function TokenBar({ used, limit, label }: { used: number; limit: number; label?: string }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0
  const color = pct < 50 ? 'bg-green-400' : pct < 75 ? 'bg-yellow-400' : pct < 90 ? 'bg-orange-400' : 'bg-red-400'
  return (
    <div>
      {label && <div className="text-[10px] text-white/40 mb-1">{label}</div>}
      <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[10px] text-white/30 mt-0.5">
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
  children,
}: {
  title: string
  icon: React.ReactNode
  tokens?: number
  cacheStatus?: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-white/8 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-3 py-2.5 text-xs hover:bg-white/5 transition-colors"
      >
        {open ? <ChevronDown size={12} className="text-white/40" /> : <ChevronRight size={12} className="text-white/40" />}
        <span className="text-white/50">{icon}</span>
        <span className="font-medium text-white/75">{title}</span>
        <span className="flex-1" />
        {tokens != null && (
          <span className="text-[10px] text-white/30 font-mono">{tokens.toLocaleString()} tok</span>
        )}
        {cacheStatus && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded ${
              cacheStatus === 'cached' ? 'bg-green-500/15 text-green-400' : 'bg-blue-500/15 text-blue-400'
            }`}
          >
            {cacheStatus === 'cached' ? '已缓存' : '新内容'}
          </span>
        )}
      </button>
      {open && <div className="px-3 pb-3 border-t border-white/5">{children}</div>}
    </div>
  )
}

function MessageItem({ msg }: { msg: ContextMessageInfo }) {
  const [expanded, setExpanded] = useState(false)
  const roleIcon =
    msg.role === 'user' ? '👤' : msg.role === 'assistant' ? '🤖' : msg.role === 'tool' ? '🔧' : '📋'
  const roleColor =
    msg.role === 'user'
      ? 'text-blue-300'
      : msg.role === 'assistant'
      ? 'text-green-300'
      : msg.role === 'tool'
      ? 'text-yellow-300'
      : 'text-white/50'

  const badges: React.ReactNode[] = []
  if (msg.is_compaction_summary) badges.push(<span key="c" className="text-[10px] px-1 py-0.5 rounded bg-purple-500/15 text-purple-300">压缩摘要</span>)
  if (msg.is_system_reminder) badges.push(<span key="r" className="text-[10px] px-1 py-0.5 rounded bg-cyan-500/15 text-cyan-300">系统提醒</span>)
  if (msg.tool_calls?.length) badges.push(<span key="t" className="text-[10px] px-1 py-0.5 rounded bg-orange-500/15 text-orange-300">{msg.tool_calls.length} 工具调用</span>)

  return (
    <div className="border-b border-white/5 last:border-b-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-2 py-1.5 text-xs hover:bg-white/3 transition-colors"
      >
        {expanded ? <ChevronDown size={10} className="text-white/30" /> : <ChevronRight size={10} className="text-white/30" />}
        <span>{roleIcon}</span>
        <span className={`font-mono ${roleColor}`}>{msg.role}</span>
        <span className="text-white/20">#{msg.index}</span>
        {badges}
        <span className="flex-1" />
        <span className="text-[10px] text-white/20 font-mono">{msg.estimated_tokens} tok</span>
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            msg.cache_status === 'cached' ? 'bg-green-400' : 'bg-blue-400'
          }`}
        />
      </button>
      {expanded && msg.content && (
        <div className="px-2 pb-2">
          <pre className="text-[11px] bg-black/30 rounded-lg p-2 border border-white/5 overflow-auto max-h-64 whitespace-pre-wrap text-white/60 leading-relaxed">
            {msg.content_truncated ? msg.content + `\n\n... (${msg.full_length} 字符)` : msg.content}
          </pre>
          {msg.tool_calls?.map((tc) => (
            <div key={tc.id} className="mt-1 text-[10px] text-white/40 font-mono">
              → {tc.tool}({tc.args_preview})
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ContextViewer({ snapshot, onClose }: Props) {
  const { system, tools, messages, summary, actual_usage, iteration, model } = snapshot

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div className="flex-1" onClick={onClose} />

      {/* Panel */}
      <div className="w-[520px] max-w-[90vw] h-full bg-[#0d1520] border-l border-white/8 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-white/8">
          <Database size={16} className="text-blue-300" />
          <div className="flex-1">
            <div className="text-sm font-medium text-white/85">上下文查看器</div>
            <div className="text-[10px] text-white/30">
              迭代 #{iteration} · {model}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Token usage bar */}
        <div className="px-4 py-3 border-b border-white/5">
          <TokenBar used={summary.total_estimated_tokens} limit={summary.context_limit} label="上下文窗口用量" />
          <div className="flex gap-4 mt-2 text-[10px]">
            <div className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-green-400" />
              <span className="text-white/40">已缓存 {summary.cached_estimated_tokens.toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-blue-400" />
              <span className="text-white/40">新内容 {summary.new_estimated_tokens.toLocaleString()}</span>
            </div>
          </div>
          {actual_usage && (
            <div className="flex gap-3 mt-2 text-[10px] text-white/30 border-t border-white/5 pt-2">
              <span>实际: 输入 {actual_usage.input_tokens.toLocaleString()}</span>
              <span>输出 {actual_usage.output_tokens.toLocaleString()}</span>
              {actual_usage.cache_read_tokens > 0 && <span>缓存读 {actual_usage.cache_read_tokens.toLocaleString()}</span>}
              {actual_usage.total_cost > 0 && <span>${actual_usage.total_cost.toFixed(4)}</span>}
            </div>
          )}
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {/* System Prompt */}
          <Section
            title="System Prompt"
            icon={<FileText size={13} />}
            tokens={system.estimated_tokens}
            cacheStatus={system.cache_status}
          >
            <pre className="text-[11px] bg-black/30 rounded-lg p-2 border border-white/5 overflow-auto max-h-64 whitespace-pre-wrap text-white/50 mt-2 leading-relaxed">
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
                <span key={name} className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/50 font-mono border border-white/5">
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
            <div className="mt-2 border border-white/5 rounded-lg overflow-hidden">
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
