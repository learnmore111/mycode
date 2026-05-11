import { useState } from 'react'
import { Sparkles, ArrowRight, PauseCircle, Play, FileCode2, X, ChevronDown, ChevronUp, Check, Undo2, Loader2 } from 'lucide-react'
import type { AgentInfo, ContextSnapshot, GitChangedFile, Message, PausedRun, Session, SessionCodeChange, StreamingPart } from '../types'
import { stageGitFile, revertGitFile } from '../api/git'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import ContextViewer from './ContextViewer'

interface Props {
  session: Session | null
  directory?: string | null
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
  onCodeChangesCleared?: () => void
  chatStatus: 'idle' | 'streaming' | 'paused'
  onCreate: () => Promise<Session | null>
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
  onRollback?: (turn: number, options?: { restoreSnapshot?: boolean }) => Promise<unknown> | void
  gitChangedPaths?: Set<string>
  gitFilesByPath?: Map<string, GitChangedFile>
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
  gitChangedPaths,
  gitFilesByPath,
  directory,
}: {
  pausedRun: PausedRun | null
  codeChanges: SessionCodeChange[]
  onResume: () => void
  onDismissPausedRun: () => void
  onSelectFile?: (path: string) => void
  onRefreshGit?: () => void
  gitChangedPaths?: Set<string>
  gitFilesByPath?: Map<string, GitChangedFile>
  directory?: string | null
}) {
  const [expanded, setExpanded] = useState(false)
  const [busyFiles, setBusyFiles] = useState<Record<string, 'stage' | 'revert'>>({})
  const [batchBusy, setBatchBusy] = useState<'stage' | 'revert' | null>(null)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  if (!pausedRun && codeChanges.length === 0) return null

  const confirmablePaths = codeChanges
    .map((c) => c.filePath)
    .filter((p): p is string => {
      if (!p) return false
      const gitFile = gitFilesByPath?.get(p)
      return !!gitFile && (!gitFile.staged || gitFile.unstaged)
    })

  const handleStage = async (path: string) => {
    setBusyFiles((prev) => ({ ...prev, [path]: 'stage' }))
    try {
      await stageGitFile(path, directory ?? undefined)
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
      await revertGitFile(path, directory ?? undefined)
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

  const handleStageAll = async () => {
    if (confirmablePaths.length === 0) return
    setBatchBusy('stage')
    try {
      // Execute sequentially to avoid git index race condition
      for (const p of confirmablePaths) {
        await stageGitFile(p, directory ?? undefined)
      }
      onRefreshGit?.()
      setExpanded(false)
      setToast({ type: 'success', message: `已确认 ${confirmablePaths.length} 个文件的更改` })
      setTimeout(() => setToast(null), 2500)
    } catch (err) {
      console.error('Stage all failed', err)
      setToast({ type: 'error', message: '确认失败，请重试' })
      setTimeout(() => setToast(null), 2500)
    } finally {
      setBatchBusy(null)
    }
  }

  const handleRevertAll = async () => {
    const filePaths = codeChanges.map((c) => c.filePath).filter((p): p is string => !!p)
    if (filePaths.length === 0) return
    if (!confirm(`确定要丢弃全部 ${filePaths.length} 个文件的更改？此操作不可撤销。`)) return
    setBatchBusy('revert')
    try {
      await Promise.all(filePaths.map((p) => revertGitFile(p, directory ?? undefined)))
      onRefreshGit?.()
      setExpanded(false)
      setToast({ type: 'success', message: `已回退 ${filePaths.length} 个文件的更改` })
      setTimeout(() => setToast(null), 2500)
    } catch (err) {
      console.error('Revert all failed', err)
      setToast({ type: 'error', message: '回退失败，请重试' })
      setTimeout(() => setToast(null), 2500)
    } finally {
      setBatchBusy(null)
    }
  }

  return (
    <div className="mx-4 mb-3 rounded-xl border border-line bg-surface-0 overflow-hidden">
      {/* Toggle bar */}
      <div className="flex items-center justify-between px-3.5 py-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-xs text-ink-secondary hover:text-ink transition-colors"
        >
          <FileCode2 size={12} className="text-accent" />
          <span className="font-medium">
            {pausedRun ? '会话已暂停' : `${codeChanges.length} 处近期改动`}
          </span>
          {expanded ? <ChevronDown size={12} className="text-ink-muted" /> : <ChevronUp size={12} className="text-ink-muted" />}
        </button>

        {codeChanges.some((change) => !!change.filePath) && (
          <div className="flex items-center gap-1">
            {batchBusy ? (
              <Loader2 size={12} className="animate-spin text-ink-muted" />
            ) : (
              <>
                <button
                  onClick={handleStageAll}
                  disabled={confirmablePaths.length === 0}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xxs font-medium text-status-success hover:bg-status-success-light transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title="全部确认 (git add)"
                >
                  <Check size={11} />
                  <span>全部确认</span>
                </button>
                <button
                  onClick={handleRevertAll}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xxs font-medium text-status-error hover:bg-status-error-light transition-colors"
                  title="全部回退 (丢弃更改)"
                >
                  <Undo2 size={11} />
                  <span>全部回退</span>
                </button>
              </>
            )}
          </div>
        )}
      </div>

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
                const gitFile = change.filePath ? gitFilesByPath?.get(change.filePath) : undefined
                const isStale =
                  !!change.filePath &&
                  !!gitChangedPaths &&
                  gitChangedPaths.size > 0 &&
                  !gitChangedPaths.has(change.filePath)
                const isConfirmed = !!gitFile && gitFile.staged && !gitFile.unstaged
                const handleClick = () => {
                  if (!change.filePath) return
                  if (isStale) {
                    // The file is no longer in git's changed-file list —
                    // tell the user explicitly instead of firing a request
                    // that will silently 404.
                    onRefreshGit?.()
                    alert(
                      `文件「${change.filePath}」已不在 Git 改动列表中。\n\n` +
                        '可能原因：\n' +
                        '• 文件已被删除且从未被 Git 跟踪\n' +
                        '• 改动已提交或已回退\n' +
                        '• 会话之后工作区已被重置',
                    )
                    return
                  }
                  onSelectFile?.(change.filePath)
                }
                return (
                  <div
                    key={change.id}
                    className="flex items-center gap-2 px-3.5 py-1.5 hover:bg-surface-hover transition-colors group"
                  >
                    <FileCode2
                      size={12}
                      className={`flex-shrink-0 ${isStale ? 'text-ink-faint/60' : 'text-ink-faint'}`}
                    />
                    <button
                      onClick={handleClick}
                      className={`flex-1 min-w-0 text-left ${
                        change.filePath ? 'hover:text-accent cursor-pointer' : 'cursor-default'
                      }`}
                      title={
                        change.filePath
                          ? isStale
                            ? '文件已不在 Git 改动列表中，点击查看详情'
                            : '查看 diff'
                          : undefined
                      }
                    >
                      <span
                        className={`text-xs font-medium truncate block ${
                          isStale ? 'text-ink-muted line-through decoration-ink-faint/50' : 'text-ink-strong'
                        }`}
                      >
                        {change.filePath || `${change.tool} 修改`}
                      </span>
                    </button>

                    {isStale && (
                      <span
                        className="text-xxs text-ink-faint flex-shrink-0"
                        title="文件已不在 Git 改动列表中"
                      >
                        已失效
                      </span>
                    )}
                    {isConfirmed && !isStale && (
                      <span
                        className="text-xxs text-status-success flex-shrink-0"
                        title="文件修改已完全确认到暂存区"
                      >
                        已确认
                      </span>
                    )}
                    <span className="text-xxs text-ink-faint font-mono flex-shrink-0">{change.tool}</span>

                    {change.filePath && !isStale && (
                      <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        {busy ? (
                          <Loader2 size={12} className="animate-spin text-ink-muted" />
                        ) : isConfirmed ? null : (
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

      {/* Toast notification */}
      {toast && (
        <div className="px-3.5 py-2 border-t border-line-subtle animate-slide-up">
          <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium ${
            toast.type === 'success'
              ? 'bg-status-success/10 text-status-success border border-status-success/20'
              : 'bg-status-error/10 text-status-error border border-status-error/20'
          }`}>
            {toast.type === 'success' ? <Check size={12} /> : <X size={12} />}
            <span>{toast.message}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ChatArea({
  session,
  directory,
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
  onRollback,
  gitChangedPaths,
  gitFilesByPath,
}: Props) {
  const [showContext, setShowContext] = useState(false)

  if (!session) {
    return (
      <div className="flex-1 flex flex-col min-h-0 bg-surface-1">
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
    <div className="flex-1 flex flex-col min-w-0 min-h-0 bg-surface-1">
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
        onRollback={onRollback}
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
        gitChangedPaths={gitChangedPaths}
        gitFilesByPath={gitFilesByPath}
        directory={directory}
      />
      {showContext && contextSnapshot && (
        <ContextViewer snapshot={contextSnapshot} sessionId={session.id} onClose={() => setShowContext(false)} />
      )}
    </div>
  )
}
