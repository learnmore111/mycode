import { ShieldAlert } from 'lucide-react'
import type { PermissionRequest } from '../types'

interface Props {
  request: PermissionRequest
  onReply: (requestId: string, action: 'allow' | 'reject' | 'always') => void
}

export default function PermissionModal({ request, onReply }: Props) {
  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-surface-1 border border-border rounded-xl max-w-md w-full shadow-modal">
        <div className="flex items-center gap-3 p-4 border-b border-border-subtle">
          <ShieldAlert size={20} className="text-accent-amber" />
          <h3 className="font-semibold text-text-primary">需要授权</h3>
        </div>

        <div className="p-4 space-y-3">
          <div>
            <div className="text-xs uppercase font-medium text-text-muted tracking-wider mb-1">操作</div>
            <div className="text-sm text-text-primary">{request.permission}</div>
          </div>

          {request.patterns.length > 0 && (
            <div>
              <div className="text-xs uppercase font-medium text-text-muted tracking-wider mb-1">匹配模式</div>
              <div className="space-y-1">
                {request.patterns.map((p, i) => (
                  <code key={i} className="block text-sm bg-surface-0 px-3 py-1.5 rounded-md text-text-secondary border border-border-subtle font-mono">
                    {p}
                  </code>
                ))}
              </div>
            </div>
          )}

          {request.metadata && Object.keys(request.metadata).length > 0 && (
            <div>
              <div className="text-xs uppercase font-medium text-text-muted tracking-wider mb-1">详情</div>
              <pre className="text-sm bg-surface-0 p-3 rounded-md max-h-32 overflow-auto text-text-secondary border border-border-subtle font-mono">
                {JSON.stringify(request.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <div className="flex gap-2 p-4 border-t border-border-subtle">
          <button
            onClick={() => onReply(request.id, 'reject')}
            className="flex-1 px-3 py-2.5 text-sm rounded-lg bg-surface-2 hover:bg-surface-3 text-text-secondary border border-border-subtle hover:border-border transition-colors"
          >
            拒绝
          </button>
          <button
            onClick={() => onReply(request.id, 'allow')}
            className="flex-1 px-3 py-2.5 text-sm rounded-lg bg-accent-blue/15 hover:bg-accent-blue/25 text-accent-blue border border-accent-blue/25 transition-colors"
          >
            允许一次
          </button>
          <button
            onClick={() => onReply(request.id, 'always')}
            className="flex-1 px-3 py-2.5 text-sm rounded-lg bg-accent-green/15 hover:bg-accent-green/25 text-accent-green border border-accent-green/25 transition-colors"
          >
            始终允许
          </button>
        </div>
      </div>
    </div>
  )
}
