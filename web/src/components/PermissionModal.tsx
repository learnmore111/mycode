import { ShieldAlert } from 'lucide-react'
import type { PermissionRequest } from '../types'

interface Props {
  request: PermissionRequest
  onReply: (requestId: string, action: 'allow' | 'reject' | 'always') => void
}

export default function PermissionModal({ request, onReply }: Props) {
  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="glass-card rounded-2xl max-w-md w-full shadow-2xl">
        <div className="flex items-center gap-3 p-4 border-b border-white/10">
          <ShieldAlert size={20} className="text-yellow-400" />
          <h3 className="font-medium text-white">需要授权</h3>
        </div>

        <div className="p-4 space-y-3">
          <div>
            <div className="text-xs text-white/40 uppercase mb-1">操作</div>
            <div className="text-sm text-white/80">{request.permission}</div>
          </div>

          {request.patterns.length > 0 && (
            <div>
              <div className="text-xs text-white/40 uppercase mb-1">匹配模式</div>
              <div className="space-y-1">
                {request.patterns.map((p, i) => (
                  <code key={i} className="block text-xs bg-black/30 px-2 py-1 rounded text-white/60 border border-white/5">
                    {p}
                  </code>
                ))}
              </div>
            </div>
          )}

          {request.metadata && Object.keys(request.metadata).length > 0 && (
            <div>
              <div className="text-xs text-white/40 uppercase mb-1">详情</div>
              <pre className="text-xs bg-black/30 p-2 rounded max-h-32 overflow-auto text-white/60 border border-white/5">
                {JSON.stringify(request.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <div className="flex gap-2 p-4 border-t border-white/10">
          <button
            onClick={() => onReply(request.id, 'reject')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-white/5 hover:bg-white/10 text-white/60 transition-colors"
          >
            拒绝
          </button>
          <button
            onClick={() => onReply(request.id, 'allow')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-blue-500/20 hover:bg-blue-500/30 text-blue-300 transition-colors"
          >
            允许一次
          </button>
          <button
            onClick={() => onReply(request.id, 'always')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-green-500/20 hover:bg-green-500/30 text-green-300 transition-colors"
          >
            始终允许
          </button>
        </div>
      </div>
    </div>
  )
}
