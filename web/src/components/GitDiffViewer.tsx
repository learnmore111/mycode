import { useMemo } from 'react'
import { FileCode2, GitBranch, Minus, Plus, RefreshCcw, Trash2, X } from 'lucide-react'
import type { GitDiffDetail } from '../types'

interface Props {
  diff: GitDiffDetail | null
  loading: boolean
  error: string | null
  onClose: () => void
}

function statusLabel(status: GitDiffDetail['status']) {
  switch (status) {
    case 'added':
      return '新增'
    case 'deleted':
      return '删除'
    case 'renamed':
      return '重命名'
    case 'untracked':
      return '未跟踪'
    case 'conflicted':
      return '冲突'
    default:
      return '修改'
  }
}

function lineClass(line: string) {
  if (line.startsWith('@@')) return 'bg-status-warning-light/70 text-status-warning'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'bg-status-success-light/80 text-status-success'
  if (line.startsWith('-') && !line.startsWith('---')) return 'bg-status-error-light/80 text-status-error'
  if (line.startsWith('diff --git') || line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')) {
    return 'bg-surface-2 text-ink-secondary'
  }
  return 'text-ink-secondary'
}

export default function GitDiffViewer({ diff, loading, error, onClose }: Props) {
  const lines = useMemo(() => (diff?.diff ? diff.diff.split('\n') : []), [diff?.diff])

  if (!loading && !error && !diff) return null

  return (
    <div className="fixed inset-0 z-50 flex animate-fade-in">
      <div className="flex-1 bg-black/10 backdrop-blur-sm" onClick={onClose} />
      <div className="w-[760px] max-w-[94vw] h-full bg-surface-1 border-l border-line flex flex-col shadow-overlay animate-slide-in-right">
        <div className="flex items-center gap-3 px-5 py-4 border-b border-line bg-surface-0">
          <div className="w-8 h-8 rounded-xl bg-accent-light flex items-center justify-center shadow-xs">
            <FileCode2 size={15} className="text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-bold text-ink-strong">Git Diff</div>
            <div className="text-xxs text-ink-muted font-mono truncate">
              {diff?.path || '正在加载改动文件...'}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-3">
            <RefreshCcw size={18} className="animate-spin text-accent" />
            <span className="text-sm text-ink-muted">加载 diff 中...</span>
          </div>
        ) : error ? (
          <div className="p-5">
            <div className="rounded-xl border border-status-error/15 bg-status-error-light px-4 py-3 text-sm text-status-error whitespace-pre-line">
              {error}
            </div>
          </div>
        ) : diff ? (
          <>
            <div className="px-5 py-4 border-b border-line bg-surface-0 space-y-3">
              <div className="flex flex-wrap items-center gap-2.5 text-xs text-ink-muted">
                <span className="inline-flex items-center gap-1 rounded-lg bg-surface-2 px-2.5 py-1 border border-line-subtle">
                  <GitBranch size={11} />
                  <span>{diff.branch || 'Detached HEAD'}</span>
                </span>
                <span className="inline-flex items-center gap-1 rounded-lg bg-surface-2 px-2.5 py-1 border border-line-subtle">
                  <span>{statusLabel(diff.status)}</span>
                </span>
                {diff.staged && <span className="inline-flex items-center rounded-lg bg-surface-2 px-2.5 py-1 border border-line-subtle">暂存区</span>}
                {diff.unstaged && <span className="inline-flex items-center rounded-lg bg-surface-2 px-2.5 py-1 border border-line-subtle">工作区</span>}
                {diff.oldPath && <span className="truncate">来自 {diff.oldPath}</span>}
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="inline-flex items-center gap-1 rounded-lg bg-status-success-light px-2.5 py-1 text-status-success border border-status-success/10">
                  <Plus size={11} />
                  <span>{diff.stats.additions}</span>
                </span>
                <span className="inline-flex items-center gap-1 rounded-lg bg-status-error-light px-2.5 py-1 text-status-error border border-status-error/10">
                  <Minus size={11} />
                  <span>{diff.stats.deletions}</span>
                </span>
                {diff.stats.isBinary && (
                  <span className="inline-flex items-center rounded-lg bg-surface-2 px-2.5 py-1 text-ink-muted border border-line-subtle">
                    二进制文件
                  </span>
                )}
                {diff.tooLarge && (
                  <span className="text-status-warning">diff 已截断，仅展示前 120000 个字符</span>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-auto bg-[#0f1720] text-slate-100">
              {diff.status === 'deleted' && lines.length === 0 ? (
                <div className="h-full flex items-center justify-center px-6">
                  <div className="max-w-md w-full rounded-2xl border border-status-error/20 bg-status-error-light px-5 py-6 text-center space-y-3">
                    <div className="mx-auto w-10 h-10 rounded-xl bg-status-error/10 flex items-center justify-center">
                      <Trash2 size={18} className="text-status-error" />
                    </div>
                    <div className="text-sm font-semibold text-status-error">文件已删除</div>
                    <div className="text-xs text-status-error/80 font-mono break-all">{diff.path}</div>
                    <div className="text-xs text-ink-secondary">
                      该文件从未在 Git 中记录过内容变化，因此没有可展示的 diff。<br />
                      如果这是预期的删除，可在下方改动面板中「暂存」或「回退」。
                    </div>
                  </div>
                </div>
              ) : lines.length > 0 ? (
                <div className="min-w-max py-4">
                  {lines.map((line, index) => (
                    <div
                      key={`${index}-${line.slice(0, 12)}`}
                      className={`px-4 py-0.5 font-mono text-xs whitespace-pre ${lineClass(line)}`}
                    >
                      {line || ' '}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-sm text-slate-400 px-6 text-center">
                  当前文件没有可展示的文本 diff，可能是二进制变更或 Git 仅记录了元数据变化。
                </div>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
