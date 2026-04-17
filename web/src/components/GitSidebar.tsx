import { useMemo, useState } from 'react'
import { AlertCircle, FileCode2, GitBranch, RefreshCcw, Search, Sparkles } from 'lucide-react'
import type { GitChangedFile, GitStatus } from '../types'

interface Props {
  status: GitStatus | null
  loading: boolean
  error: string | null
  selectedPath: string | null
  onSelectFile: (path: string) => void
  onRefresh: () => void
}

const STATUS_LABELS: Record<GitChangedFile['status'], string> = {
  modified: 'M',
  added: 'A',
  deleted: 'D',
  renamed: 'R',
  untracked: 'U',
  conflicted: '!',
}

const STATUS_STYLES: Record<GitChangedFile['status'], string> = {
  modified: 'bg-status-warning-light text-status-warning border-status-warning/15',
  added: 'bg-status-success-light text-status-success border-status-success/15',
  deleted: 'bg-status-error-light text-status-error border-status-error/15',
  renamed: 'bg-status-info-light text-status-info border-status-info/15',
  untracked: 'bg-accent-light text-accent border-accent/15',
  conflicted: 'bg-status-error-light text-status-error border-status-error/20',
}

function splitPath(path: string) {
  const parts = path.split('/')
  const name = parts.pop() || path
  return { name, dir: parts.join('/') }
}

function SummaryBadge({ label, value }: { label: string; value: number }) {
  if (!value) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-lg border border-line-subtle bg-surface-1 px-2 py-1 text-[11px] text-ink-muted">
      <span className="font-semibold text-ink-secondary">{value}</span>
      <span>{label}</span>
    </span>
  )
}

export default function GitSidebar({ status, loading, error, selectedPath, onSelectFile, onRefresh }: Props) {
  const [query, setQuery] = useState('')

  const filteredFiles = useMemo(() => {
    const files = status?.files ?? []
    if (!query.trim()) return files
    const keyword = query.trim().toLowerCase()
    return files.filter((item) => item.path.toLowerCase().includes(keyword) || (item.oldPath ?? '').toLowerCase().includes(keyword))
  }, [query, status?.files])

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4 py-10">
        <RefreshCcw size={16} className="animate-spin text-accent" />
        <span className="text-xs text-ink-muted">加载 Git 信息...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 px-3 py-4">
        <div className="rounded-xl border border-status-error/15 bg-status-error-light px-3.5 py-3 text-xs text-status-error">
          {error}
        </div>
      </div>
    )
  }

  if (!status?.available) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
        <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
          <GitBranch size={20} className="text-ink-faint" />
        </div>
        <div>
          <div className="text-sm text-ink-secondary">当前目录没有 Git 仓库</div>
          <div className="text-xs text-ink-faint mt-1">{status?.reason || '初始化仓库后这里会显示分支和改动文件。'}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 pb-3 space-y-3">
        <div className="rounded-2xl border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
                <GitBranch size={14} className="text-accent" />
                <span>{status.branch || 'Detached HEAD'}</span>
              </div>
              <div className="mt-1 text-[11px] text-ink-muted font-mono">
                {status.head ? `HEAD ${status.head}` : '暂无提交信息'}
              </div>
            </div>
            <button
              onClick={onRefresh}
              className="p-2 rounded-xl text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              title="刷新 Git 状态"
            >
              <RefreshCcw size={13} />
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <SummaryBadge label="改动" value={status.summary.changed} />
            <SummaryBadge label="暂存" value={status.summary.staged} />
            <SummaryBadge label="未跟踪" value={status.summary.untracked} />
            <SummaryBadge label="冲突" value={status.summary.conflicted} />
          </div>
          {(status.ahead > 0 || status.behind > 0) && (
            <div className="mt-3 text-[11px] text-ink-muted">
              相对 {status.upstream || '远端'}：
              {status.ahead > 0 && <span className="ml-1 text-status-success">领先 {status.ahead}</span>}
              {status.behind > 0 && <span className="ml-2 text-status-warning">落后 {status.behind}</span>}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 bg-surface-2 rounded-lg px-3 py-2 border border-line-subtle focus-within:border-accent/30 focus-within:bg-surface-0 transition-all">
          <Search size={12} className="text-ink-muted flex-shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="过滤改动文件..."
            className="flex-1 bg-transparent text-xs text-ink placeholder:text-ink-muted outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {status.clean ? (
          <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
            <div className="w-12 h-12 rounded-2xl bg-status-success-light flex items-center justify-center">
              <Sparkles size={20} className="text-status-success" />
            </div>
            <div>
              <div className="text-sm text-ink-secondary">工作区很干净</div>
              <div className="text-xs text-ink-faint mt-1">当前没有待查看的文件改动。</div>
            </div>
          </div>
        ) : filteredFiles.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-5 py-10 text-center">
            <Search size={18} className="text-ink-faint" />
            <div className="text-xs text-ink-muted">没有匹配的 Git 改动文件</div>
          </div>
        ) : (
          <div className="space-y-1.5">
            {filteredFiles.map((file) => {
              const { name, dir } = splitPath(file.path)
              const active = selectedPath === file.path
              return (
                <button
                  key={`${file.path}:${file.status}`}
                  onClick={() => onSelectFile(file.path)}
                  className={`w-full rounded-xl border px-3 py-2.5 text-left transition-all ${
                    active
                      ? 'border-accent/25 bg-accent-light/70 shadow-xs'
                      : 'border-transparent bg-surface-1 hover:border-line hover:bg-surface-hover'
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <div className={`mt-0.5 flex h-6 w-6 items-center justify-center rounded-lg border text-[11px] font-semibold ${STATUS_STYLES[file.status]}`}>
                      {STATUS_LABELS[file.status]}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-ink-strong">{name}</span>
                        {file.status === 'conflicted' && <AlertCircle size={12} className="text-status-error flex-shrink-0" />}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-ink-faint font-mono">
                        {dir || '项目根目录'}
                      </div>
                      {file.oldPath && (
                        <div className="mt-1 truncate text-[11px] text-ink-muted">
                          重命名自 {file.oldPath}
                        </div>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-1 text-[10px] text-ink-muted">
                      {file.staged && <span className="rounded-md bg-surface-0 px-1.5 py-0.5 border border-line-subtle">暂存</span>}
                      {file.unstaged && <span className="rounded-md bg-surface-0 px-1.5 py-0.5 border border-line-subtle">工作区</span>}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-t border-line-subtle">
        <div className="flex items-center justify-between text-xxs text-ink-faint">
          <span>共 {status.summary.changed} 处改动</span>
          <span className="font-mono flex items-center gap-1"><FileCode2 size={10} /> diff</span>
        </div>
      </div>
    </div>
  )
}
