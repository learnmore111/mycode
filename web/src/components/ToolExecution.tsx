import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, CheckCircle2, XCircle, Circle, FileCode2 } from 'lucide-react'
import type { Part } from '../types'
import { extractToolTargetFile } from '../utils/sessionInsights'

interface Props {
  part: Part
}

export default function ToolExecution({ part }: Props) {
  const [expanded, setExpanded] = useState(false)
  const status = part.state?.status ?? 'unknown'
  const toolName = part.tool ?? 'unknown'
  const input = part.state?.input as string | object | undefined
  const output = (part.state?.output ?? part.content) as string | undefined
  const targetFile = extractToolTargetFile(part)

  const statusIcon = () => {
    switch (status) {
      case 'running':
        return <Loader2 size={13} className="animate-spin text-status-warning" />
      case 'completed':
      case 'success':
        return <CheckCircle2 size={13} className="text-status-success" />
      case 'error':
        return <XCircle size={13} className="text-status-error" />
      default:
        return <Circle size={13} className="text-ink-faint" />
    }
  }

  const statusBg = () => {
    switch (status) {
      case 'running':
        return 'bg-status-warning-light border-status-warning/15'
      case 'completed':
      case 'success':
        return 'bg-status-success-light border-status-success/15'
      case 'error':
        return 'bg-status-error-light border-status-error/15'
      default:
        return 'bg-surface-2 border-line'
    }
  }

  return (
    <div className={`my-2.5 rounded-lg border overflow-hidden ${statusBg()}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2.5 w-full px-3.5 py-2 text-sm hover:bg-black/[0.02] transition-colors"
      >
        {expanded ? (
          <ChevronDown size={13} className="text-ink-muted" />
        ) : (
          <ChevronRight size={13} className="text-ink-muted" />
        )}
        {statusIcon()}
        <span className="font-mono text-xs font-medium text-ink-secondary">{toolName}</span>
        {targetFile && (
          <span className="inline-flex items-center gap-1 rounded-md border border-line bg-surface-0 px-2 py-0.5 text-[11px] text-ink-muted max-w-[260px] truncate">
            <FileCode2 size={10} className="text-accent flex-shrink-0" />
            <span className="truncate">{targetFile}</span>
          </span>
        )}
        {!expanded && input && (
          <span className="text-ink-muted truncate ml-1 text-xs">
            {typeof input === 'string' ? input.slice(0, 60) : JSON.stringify(input).slice(0, 60)}
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-3.5 pb-3 space-y-2.5 border-t border-black/[0.04]">
          {targetFile && (
            <div className="mt-2.5">
              <div className="text-xxs uppercase font-mono font-semibold text-ink-muted tracking-wider mb-1.5">修改文件</div>
              <div className="text-xs bg-surface-0 p-3 rounded-lg border border-line text-ink-secondary font-mono leading-relaxed break-all">
                {targetFile}
              </div>
            </div>
          )}
          {input && (
            <div>
              <div className="text-xxs uppercase font-mono font-semibold text-ink-muted tracking-wider mb-1.5">输入</div>
              <pre className="text-xs bg-surface-0 p-3 rounded-lg border border-line overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap text-ink-secondary font-mono leading-relaxed">
                {typeof input === 'string' ? input : JSON.stringify(input, null, 2)}
              </pre>
            </div>
          )}
          {output && (
            <div>
              <div className="text-xxs uppercase font-mono font-semibold text-ink-muted tracking-wider mb-1.5">输出</div>
              <pre className="text-xs bg-surface-0 p-3 rounded-lg border border-line overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap text-ink-secondary font-mono leading-relaxed">
                {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
              </pre>
            </div>
          )}
          {part.state?.error && (
            <div>
              <div className="text-xxs uppercase font-mono font-semibold text-status-error tracking-wider mb-1.5">错误</div>
              <pre className="text-xs bg-status-error-light p-3 rounded-lg border border-status-error/15 text-status-error whitespace-pre-wrap font-mono leading-relaxed">
                {part.state.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
