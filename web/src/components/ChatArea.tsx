import { useState } from 'react'
import { Sparkles, ArrowRight, PauseCircle, Play, FileCode2, X, ChevronDown, ChevronUp, Check, Undo2, Loader2 } from 'lucide-react'
import type { AgentInfo, ContextSnapshot, Message, PausedRun, Session, SessionCodeChange, StreamingPart } from '../types'
import { stageGitFile, revertGitFile } from '../api/git'
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
  onSelectGitFile?: (path: string) => void
  onRefreshGit?: () => void
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
  onSelectFile,
  onRefreshGit,
}: {
  pausedRun: PausedRun | null
  codeChanges: SessionCodeChange[]
  onResume: () => void
  onDismissPausedRun: () => void
  onSelectFile?: (path: string) => void
  onRefreshGit?: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [busyFiles, setBusyFiles] = useState<Record<string, 'stage' | 'revert'>>({})

  if (!pausedRun && codeChanges.length === 0) return null

  const handleStage = async (path: string) => {
    setBusyFiles((prev) => ({ ...prev, [path]: 'stage' }))
    try {
      await stageGitFile(path)
      onRefreshGit?.()
    } catch (err) {
      console.error('Stage failed', err)
    } finally {
      setBusyFiles((prev) => {
        const next = { ...prev }
        delete next[path]
        return next
      })
    }
  }

  const handleRevert = async (path: string) => {
    if (!confirm(`确定要丢弃 ${path} 的所有更改？此操作不可撤销。`)) return
    setBusyFiles((prev) => ({ ...prev, [path]: 'revert' }))
    try {
      await revertGitFile(path)
      onRefreshGit?.()
    } catch (err) {
      console.error('Revert failed', err)
    } finally {
      setBusyFiles((prev) => {
        const next = { ...prev }
        delete next[path]
        return next
      })
    }
  }

  return (
    <div className="mx-4 mb-3 rounded-xl border border-line bg-surface-0 overflow-hidden">
      {/* Toggle bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3.5 py-2 hover:bg-surface-hover transition-colors"
      >
        <div className="flex items-center gap-2 text-xs text-ink-secondary">
          <FileCode2 size={12} className="text-accent" />
          <span className="font-medium">
            {pausedRun ? '会话已暂停' : `${codeChanges.length} 处近期改动`}
          </span>
        </div>
        {expanded ? <ChevronDown size={12} className="text-ink-muted" /> : <ChevronUp size={12} className="text-ink-muted" />}
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-line-subtle animate-slide-up">
          {pausedRun && (
            <div className="mx-3.5 mt-2.5 rounded-lg bg-status-warning-light border border-status-warning/15 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-status-warning text-xs font-semibold">
                  <PauseCircle size={11} />
                  <span>暂停前的最后请求</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={onDismissPausedRun}
                    className="p-1 rounded-lg text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
                    title="忽略"
                  >
                    <X size={12} />
                  </button>
                  <button
                    onClick={onResume}
                    className="flex items-center gap-1 px-2 py-1 rounded-lg bg-accent text-white text-xxs font-medium hover:bg-accent-hover transition-colors"
                  >
                    <Play size={10} />
                    <span>恢复</span>
                  </button>
                </div>
              </div>
              <div className="text-xs text-ink-secondary mt-1.5 line-clamp-2">{pausedRun.lastUserText}</div>
            </div>
          )}

          {/* Vertical file list */}
          {codeChanges.length > 0 && (
            <div className="py-1.5">
              {codeChanges.map((change) => {
                const busy = change.filePath ? busyFiles[change.filePath] : undefined
                return (
                  <div
                    key={change.id}
                    className="flex items-center gap-2 px-3.5 py-1.5 hover:bg-surface-hover transition-colors group"
                  >
                    <FileCode2 size={12} className="text-ink-faint flex-shrink-0" />
                    <button
                      onClick={() => change.filePath && onSelectFile?.(change.filePath)}
                      className={`flex-1 min-w-0 text-left ${
                        change.filePath ? 'hover:text-accent cursor-pointer' : 'cursor-default'
                      }`}
                      title={change.filePath ? `查看 diff` : undefined}
                    >
                      <span className="text-xs font-medium text-ink-strong truncate block">
                        {change.filePath || `${change.tool} 修改`}
                      </span>
                    </button>

                    <span className="text-xxs text-ink-faint font-mono flex-shrink-0">{change.tool}</span>

                    {change.filePath && (
                      <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        {busy ? (
                          <Loader2 size={12} className="animate-spin text-ink-muted" />
                        ) : (
                          <>
                            <button
                              onClick={() => handleStage(change.filePath!)}
                              className="p-1 rounded-md text-ink-muted hover:bg-status-success-light hover:text-status-success transition-colors"
                              title="确认 (git add)"
                            >
                              <Check size={12} />
                            </button>
                            <button
                              onClick={() => handleRevert(change.filePath!)}
                              className="p-1 rounded-md text-ink-muted hover:bg-status-error-light hover:text-status-error transition-colors"
                              title="回退 (丢弃更改)"
                            >
                              <Undo2 size={12} />
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
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
  onSelectGitFile,
  onRefreshGit,
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
      <div className="px-4 pb-3">
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
      <ChangesPanel
        pausedRun={pausedRun}
        codeChanges={codeChanges}
        onResume={onResume}
        onDismissPausedRun={onDismissPausedRun}
        onSelectFile={onSelectGitFile}
        onRefreshGit={onRefreshGit}
      />
      {showContext && contextSnapshot && (
        <ContextViewer snapshot={contextSnapshot} sessionId={session.id} onClose={() => setShowContext(false)} />
      )}
    </div>
  )
}
