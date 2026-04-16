import { useState } from 'react'
import { ChevronDown, ChevronRight, Terminal, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import type { Part } from '../types'

interface Props {
  part: Part
}

export default function ToolExecution({ part }: Props) {
  const [expanded, setExpanded] = useState(false)
  const status = part.state?.status ?? 'unknown'
  const toolName = part.tool ?? 'unknown'
  const input = part.state?.input as string | object | undefined
  const output = (part.state?.output ?? part.content) as string | undefined

  const StatusIcon = () => {
    switch (status) {
      case 'running':
        return <Loader2 size={12} className="animate-spin text-accent-amber" />
      case 'completed':
      case 'success':
        return <CheckCircle2 size={12} className="text-accent-green" />
      case 'error':
        return <XCircle size={12} className="text-accent-red" />
      default:
        return <Terminal size={12} className="text-text-muted" />
    }
  }

  return (
    <div className="my-3 border border-border-subtle rounded-lg bg-surface-1 shadow-card overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-surface-2 transition-colors"
      >
        {expanded ? <ChevronDown size={12} className="text-text-muted" /> : <ChevronRight size={12} className="text-text-muted" />}
        <StatusIcon />
        <span className="font-mono text-sm text-text-secondary">{toolName}</span>
        {!expanded && input && (
          <span className="text-text-muted truncate ml-2 font-mono text-xs">
            {typeof input === 'string' ? input : JSON.stringify(input).slice(0, 60)}
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-border-subtle">
          {input && (
            <div className="mt-2">
              <div className="text-xs uppercase font-medium text-text-muted tracking-wider mb-1">Input</div>
              <pre className="text-xs bg-surface-0 p-3 rounded-md border border-border-subtle overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap text-text-secondary font-mono">
                {typeof input === 'string' ? input : JSON.stringify(input, null, 2)}
              </pre>
            </div>
          )}
          {output && (
            <div>
              <div className="text-xs uppercase font-medium text-text-muted tracking-wider mb-1">Output</div>
              <pre className="text-xs bg-surface-0 p-3 rounded-md border border-border-subtle overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap text-text-secondary font-mono">
                {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
              </pre>
            </div>
          )}
          {part.state?.error && (
            <div>
              <div className="text-xs uppercase font-medium text-accent-red tracking-wider mb-1">Error</div>
              <pre className="text-xs bg-accent-red/5 p-3 rounded-md border border-accent-red/20 text-accent-red whitespace-pre-wrap font-mono">
                {part.state.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
