import { useEffect, useRef } from 'react'
import { ShieldAlert } from 'lucide-react'
import type { PermissionRequest } from '../types'

interface Props {
  request: PermissionRequest
  onReply: (requestId: string, action: 'allow' | 'reject' | 'always') => void
}

export default function PermissionModal({ request, onReply }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const rejectRef = useRef<HTMLButtonElement>(null)

  // Focus the safest ("reject") button on open so pressing Enter without
  // reading the dialog doesn't accidentally grant access.
  useEffect(() => {
    rejectRef.current?.focus()
  }, [request.id])

  // Trap focus inside the dialog while it is open — a ton of users
  // Tab through pages without looking, and letting focus escape into
  // the main chat area hides the fact that we're blocking on them.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Tab' && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        )
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      } else if (e.key === 'Escape') {
        // Escape == reject (safe default, matches CLI convention)
        e.preventDefault()
        onReply(request.id, 'reject')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onReply, request.id])

  return (
    <div
      className="fixed inset-0 bg-black/20 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in"
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="bg-surface-0 border border-line rounded-xl max-w-md w-full shadow-overlay animate-slide-up"
        role="dialog"
        aria-modal="true"
        aria-labelledby="permission-modal-title"
        aria-describedby="permission-modal-body"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-line">
          <div className="w-8 h-8 rounded-lg bg-status-warning-light flex items-center justify-center">
            <ShieldAlert size={16} className="text-status-warning" aria-hidden="true" />
          </div>
          <div>
            <h3 id="permission-modal-title" className="text-sm font-semibold text-ink-strong">需要授权</h3>
            <p className="text-xs text-ink-muted mt-0.5">以下操作需要你的确认</p>
          </div>
        </div>

        {/* Body */}
        <div id="permission-modal-body" className="p-5 space-y-3.5">
          <div>
            <div className="text-xxs uppercase font-semibold text-ink-muted tracking-wider mb-1.5">操作</div>
            <div className="text-sm text-ink font-mono bg-surface-2 px-3 py-2 rounded-lg">{request.permission}</div>
          </div>

          {request.patterns.length > 0 && (
            <div>
              <div className="text-xxs uppercase font-semibold text-ink-muted tracking-wider mb-1.5">匹配规则</div>
              <div className="space-y-1.5">
                {request.patterns.map((p, i) => (
                  <code key={i} className="block text-xs bg-surface-2 px-3 py-2 rounded-lg text-ink-secondary font-mono">
                    {p}
                  </code>
                ))}
              </div>
            </div>
          )}

          {request.metadata && Object.keys(request.metadata).length > 0 && (
            <div>
              <div className="text-xxs uppercase font-semibold text-ink-muted tracking-wider mb-1.5">详情</div>
              <pre className="text-xs bg-surface-2 p-3 rounded-lg max-h-32 overflow-auto text-ink-secondary font-mono">
                {JSON.stringify(request.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2.5 px-5 py-4 border-t border-line">
          <button
            ref={rejectRef}
            onClick={() => onReply(request.id, 'reject')}
            className="flex-1 px-3 py-2.5 text-sm font-medium rounded-lg bg-surface-2 hover:bg-surface-3 text-ink-secondary transition-colors focus:outline-none focus:ring-2 focus:ring-accent"
          >
            拒绝
          </button>
          <button
            onClick={() => onReply(request.id, 'allow')}
            className="flex-1 px-3 py-2.5 text-sm font-medium rounded-lg bg-status-info-light hover:bg-status-info/10 text-status-info transition-colors focus:outline-none focus:ring-2 focus:ring-accent"
          >
            允许一次
          </button>
          <button
            onClick={() => onReply(request.id, 'always')}
            className="flex-1 px-3 py-2.5 text-sm font-medium rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors focus:outline-none focus:ring-2 focus:ring-accent"
          >
            始终允许
          </button>
        </div>
      </div>
    </div>
  )
}
