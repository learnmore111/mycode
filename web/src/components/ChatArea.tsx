import { useState } from 'react'
import { Sparkles, ArrowRight, PauseCircle, Play, FileCode2, X } from 'lucide-react'
import type { AgentInfo, ContextSnapshot, Message, PausedRun, Session, SessionCodeChange, StreamingPart } from '../types'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import ContextViewer from './ContextViewer'

interface Props {
  session: Session | null
  messages: Message[]
  streaming: boolean
  streamText: string
  streamParts: StreamingPart[]
  error: string | null
  loadingHistory: boolean
  onSend: (text: string) => void
  onAbort: () => void
  onResume: () => void
  onDismissPausedRun: () => void
  pausedRun: PausedRun | null
  codeChanges: SessionCodeChange[]
  chatStatus: 'idle' | 'streaming' | 'paused'
  onCreate: () => Promise<Session>
  models: { id: string; name: string; provider: string }[]
  agents: AgentInfo[]
  selectedModel?: string
  selectedAgent?: string
  onModelChange: (m: string | undefined) => void
  onAgentChange: (a: string | undefined) => void
  contextSnapshot?: ContextSnapshot | null
  canReturnToLastSession?: boolean
  onReturnToLastSession?: () => void
}

const SUGGESTIONS = [
  { text: '分析当前项目的架构和技术栈', icon: '🏗️' },
  { text: '帮我写一个 REST API 接口', icon: '⚡' },
  { text: '查找并修复代码中的 bug', icon: '🔍' },
  { text: '重构这段代码，提升可读性', icon: '✨' },
]

function ChangesPanel({
  pausedRun,
  codeChanges,
  onResume,
  onDismissPausedRun,
}: {
  pausedRun: PausedRun | null
  codeChanges: SessionCodeChange[]
  onResume: () => void
  onDismissPausedRun: () => void
}) {
  if (!pausedRun && codeChanges.length === 0) return null

  return (
    <div className="mx-4 mt-4 rounded-2xl border border-line bg-surface-0 shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-line-subtle flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-ink-strong">恢复与改动回顾</div>
          <div className="text-xs text-ink-muted mt-0.5">
            {pausedRun ? '当前会话已暂停，可继续从中断处恢复。' : '这里汇总了本会话最近涉及的代码修改。'}
          </div>
        </div>
        {pausedRun && (
          <div className="flex items-center gap-2">
            <button
              onClick={onDismissPausedRun}
              className="p-2 rounded-xl text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              title="忽略暂停状态"
            >
              <X size={14} />
            </button>
            <button
              onClick={onResume}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors"
            >
              <Play size={12} />
              <span>恢复继续</span>
            </button>
          </div>
        )}
      </div>

      <div className="px-4 py-3 space-y-3">
        {pausedRun && (
          <div className="rounded-xl bg-status-warning-light border border-status-warning/15 px-3.5 py-3">
            <div className="flex items-center gap-2 text-status-warning text-xs font-semibold mb-1.5">
              <PauseCircle size={12} />
              <span>暂停前的最后请求</span>
            </div>
            <div className="text-sm text-ink-secondary leading-relaxed">{pausedRun.lastUserText}</div>
            {pausedRun.partialText && (
              <div className="mt-2 text-xs text-ink-muted line-clamp-2">
                已生成部分响应：{pausedRun.partialText}
              </div>
            )}
          </div>
        )}

        {codeChanges.length > 0 && (
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold text-ink-secondary mb-2">
              <FileCode2 size={12} className="text-accent" />
              <span>最近代码修改</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {codeChanges.map((change) => (
                <div
                  key={change.id}
                  className="max-w-full rounded-xl border border-line bg-surface-1 px-3 py-2"
                  title={change.preview || change.filePath || change.tool}
                >
                  <div className="text-xs font-medium text-ink-strong truncate max-w-[280px]">
                    {change.filePath || `${change.tool} 修改`}
                  </div>
                  <div className="text-xxs text-ink-muted mt-0.5">
                    {change.tool === 'summary' ? '会话摘要' : `工具：${change.tool}`}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function ChatArea({
  session,
  messages,
  streaming,
  streamText,
  streamParts,
  error,
  loadingHistory,
  onSend,
  onAbort,
  onResume,
  onDismissPausedRun,
  pausedRun,
  codeChanges,
  chatStatus,
  onCreate,
  models,
  agents,
  selectedModel,
  selectedAgent,
  onModelChange,
  onAgentChange,
  contextSnapshot,
  canReturnToLastSession = false,
  onReturnToLastSession,
}: Props) {
  const [showContext, setShowContext] = useState(false)

  if (!session) {
    return (
      <div className="flex-1 flex flex-col bg-surface-1">
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          <div className="mb-8 flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-accent flex items-center justify-center shadow-sm">
              <Sparkles size={22} className="text-white" />
            </div>
            <h1 className="text-xl font-semibold text-ink-strong tracking-tight">有什么可以帮到你？</h1>
            <p className="text-sm text-ink-tertiary">选择一个常用指令或直接输入你的问题</p>
          </div>

          <div className="max-w-lg w-full grid grid-cols-2 gap-2.5">
            {SUGGESTIONS.map((item, i) => (
              <button
                key={i}
                onClick={async () => {
                  const sess = await onCreate()
                  if (sess) {
                    setTimeout(() => onSend(item.text), 100)
                  }
                }}
                className="group flex items-start gap-3 p-4 rounded-xl text-left bg-surface-0 border border-line-subtle hover:border-accent hover:shadow-sm transition-all"
              >
                <span className="text-lg flex-shrink-0 mt-0.5">{item.icon}</span>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-ink-secondary group-hover:text-ink transition-colors leading-snug block">{item.text}</span>
                </div>
                <ArrowRight size={14} className="text-ink-faint group-hover:text-accent transition-colors flex-shrink-0 mt-0.5" />
              </button>
            ))}
          </div>
        </div>

        <div className="px-6 pb-8">
          <MessageInput
            onSend={async (text) => {
              const sess = await onCreate()
              if (sess) {
                setTimeout(() => onSend(text), 100)
              }
            }}
            onAbort={onAbort}
            streaming={streaming}
            models={models}
            agents={agents}
            selectedModel={selectedModel}
            selectedAgent={selectedAgent}
            onModelChange={onModelChange}
            onAgentChange={onAgentChange}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-surface-1">
      <ChatHeader
        session={session}
        models={models}
        agents={agents}
        selectedModel={selectedModel}
        selectedAgent={selectedAgent}
        onModelChange={onModelChange}
        onAgentChange={onAgentChange}
        contextSnapshot={contextSnapshot}
        onViewContext={() => setShowContext(true)}
        isPaused={chatStatus === 'paused'}
        canReturnToLastSession={canReturnToLastSession}
        onReturnToLastSession={onReturnToLastSession}
      />
      <ChangesPanel
        pausedRun={pausedRun}
        codeChanges={codeChanges}
        onResume={onResume}
        onDismissPausedRun={onDismissPausedRun}
      />
      <MessageList
        messages={messages}
        streaming={streaming}
        streamText={streamText}
        streamParts={streamParts}
        loadingHistory={loadingHistory}
      />
      {error && (
        <div className="mx-4 mb-2 px-4 py-2.5 bg-status-error-light border border-status-error/15 rounded-lg text-status-error text-sm">
          {error}
        </div>
      )}
      <div className="px-4 pb-5">
        <MessageInput
          onSend={onSend}
          onAbort={onAbort}
          streaming={streaming}
          models={models}
          agents={agents}
          selectedModel={selectedModel}
          selectedAgent={selectedAgent}
          onModelChange={onModelChange}
          onAgentChange={onAgentChange}
        />
      </div>
      {showContext && contextSnapshot && (
        <ContextViewer snapshot={contextSnapshot} sessionId={session.id} onClose={() => setShowContext(false)} />
      )}
    </div>
  )
}
