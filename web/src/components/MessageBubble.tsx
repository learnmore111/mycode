import { useMemo, useState, useRef, useEffect } from 'react'
import { History, Loader2, ChevronDown, ChevronRight, Lightbulb } from 'lucide-react'
import type { Message } from '../types'
import TextContent from './TextContent'
import ToolExecution from './ToolExecution'
import MessageMeta from './MessageMeta'

interface Props {
  message: Message
  onRollback?: (turn: number, options?: { restoreSnapshot?: boolean }) => Promise<unknown> | void
  streaming?: boolean
}

function ReasoningBlock({ content, streaming }: { content: string; streaming?: boolean }) {
  const [collapsed, setCollapsed] = useState(false)
  const wasStreaming = useRef(streaming)

  useEffect(() => {
    if (wasStreaming.current && !streaming) {
      setCollapsed(true)
    }
    wasStreaming.current = streaming
  }, [streaming])

  if (!content) return null

  return (
    <div className="my-3 rounded-lg border border-accent/15 bg-accent/5 overflow-hidden">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs text-accent/80 hover:text-accent hover:bg-accent/10 transition-colors text-left"
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        <Lightbulb size={12} />
        <span className="font-medium">思考过程</span>
        {!collapsed && (
          <span className="ml-auto text-xxs text-ink-faint">{content.length} 字符</span>
        )}
      </button>
      {!collapsed && (
        <div className="px-3 pb-3 pt-0 text-sm text-ink-tertiary leading-relaxed whitespace-pre-wrap animate-fade-in">
          {content}
        </div>
      )}
    </div>
  )
}

export default function MessageBubble({ message, onRollback, streaming }: Props) {
  const isUser = message.role === 'user'
  const [rollbackBusy, setRollbackBusy] = useState(false)

  const sortedParts = useMemo(() => {
    const order: Record<string, number> = { reasoning: 0, tool: 1, text: 2 }
    return [...message.parts].sort((a, b) => (order[a.type] ?? 9) - (order[b.type] ?? 9))
  }, [message.parts])

  const canRollback =
    !isUser && !!onRollback && typeof message.turnNumber === 'number' && message.turnNumber > 0

  const handleRollback = async () => {
    if (!canRollback || !onRollback) return
    const turn = message.turnNumber as number
    const hasSnapshot = !!message.snapshotRef
    const snapshotNote = hasSnapshot
      ? '\n\n此轮保存了工作区快照，确认后会同时恢复磁盘文件。'
      : '\n\n此轮没有工作区快照，仅回退对话内容。'
    if (!confirm(`回退到第 ${turn} 轮对话？该轮之后的所有消息将被永久删除。${snapshotNote}`)) {
      return
    }
    setRollbackBusy(true)
    try {
      await onRollback(turn, { restoreSnapshot: hasSnapshot })
    } catch (err) {
      console.error('Rollback failed', err)
    } finally {
      setRollbackBusy(false)
    }
  }

  return (
    <div className="animate-fade-in group/bubble">
      {/* Role label */}
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold ${
          isUser
            ? 'bg-surface-3 text-ink-secondary'
            : 'bg-accent text-white'
        }`}>
          {isUser ? 'U' : 'A'}
        </div>
        <span className={`text-xs font-semibold ${isUser ? 'text-ink-secondary' : 'text-ink-strong'}`}>
          {isUser ? '你' : '助手'}
        </span>
        {typeof message.turnNumber === 'number' && message.turnNumber > 0 && (
          <span className="text-xxs text-ink-faint font-mono">#{message.turnNumber}</span>
        )}
        {canRollback && (
          <button
            type="button"
            onClick={handleRollback}
            disabled={rollbackBusy}
            className="ml-auto opacity-0 group-hover/bubble:opacity-100 focus:opacity-100 flex items-center gap-1 px-2 py-0.5 rounded-md text-xxs font-medium text-ink-muted hover:bg-status-warning-light hover:text-status-warning transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            title={message.snapshotRef ? '回退到此轮（同步恢复工作区快照）' : '回退到此轮（仅删除后续对话）'}
          >
            {rollbackBusy ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <History size={11} />
            )}
            <span>回退到此处</span>
          </button>
        )}
      </div>

      {/* Content */}
      <div className={`pl-7 ${isUser ? 'text-ink' : 'text-ink'}`}>
        {sortedParts.map((part) => {
          switch (part.type) {
            case 'text':
              return <TextContent key={part.id} content={part.content ?? ''} />
            case 'tool':
              return <ToolExecution key={part.id} part={part} />
            case 'reasoning':
              return <ReasoningBlock key={part.id} content={part.content ?? ''} streaming={streaming} />
            default:
              return null
          }
        })}
      </div>

      {/* Metadata */}
      {!isUser && message.tokens && (
        <div className="pl-7">
          <MessageMeta message={message} />
        </div>
      )}
    </div>
  )
}
