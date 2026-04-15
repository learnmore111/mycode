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
        return <Loader2 size={12} className="animate-spin text-yellow-400" />
      case 'success':
        return <CheckCircle2 size={12} className="text-green-400" />
      case 'error':
        return <XCircle size={12} className="text-red-400" />
      default:
        return <Terminal size={12} className="text-gray-400" />
    }
  }

  return (
    <div className="my-2 border border-gray-700 rounded-lg bg-gray-900/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-gray-800/50 transition-colors"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <StatusIcon />
        <span className="font-mono text-gray-300">{toolName}</span>
        {!expanded && input && (
          <span className="text-gray-500 truncate ml-2">
            {typeof input === 'string' ? input : JSON.stringify(input).slice(0, 60)}
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2">
          {input && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Input</div>
              <pre className="text-xs bg-gray-900 p-2 rounded border border-gray-800 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
                {typeof input === 'string' ? input : JSON.stringify(input, null, 2)}
              </pre>
            </div>
          )}
          {output && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Output</div>
              <pre className="text-xs bg-gray-900 p-2 rounded border border-gray-800 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap">
                {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
              </pre>
            </div>
          )}
          {part.state?.error && (
            <div>
              <div className="text-[10px] uppercase text-red-400 mb-1">Error</div>
              <pre className="text-xs bg-red-900/20 p-2 rounded border border-red-800 text-red-300 whitespace-pre-wrap">
                {part.state.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
