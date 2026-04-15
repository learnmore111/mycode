import { ShieldAlert } from 'lucide-react'
import type { PermissionRequest } from '../types'

interface Props {
  request: PermissionRequest
  onReply: (requestId: string, action: 'allow' | 'reject' | 'always') => void
}

export default function PermissionModal({ request, onReply }: Props) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl max-w-md w-full shadow-2xl">
        <div className="flex items-center gap-3 p-4 border-b border-gray-800">
          <ShieldAlert size={20} className="text-yellow-400" />
          <h3 className="font-medium text-gray-100">Permission Required</h3>
        </div>

        <div className="p-4 space-y-3">
          <div>
            <div className="text-xs text-gray-500 uppercase mb-1">Action</div>
            <div className="text-sm text-gray-200">{request.permission}</div>
          </div>

          {request.patterns.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 uppercase mb-1">Patterns</div>
              <div className="space-y-1">
                {request.patterns.map((p, i) => (
                  <code key={i} className="block text-xs bg-gray-800 px-2 py-1 rounded text-gray-300">
                    {p}
                  </code>
                ))}
              </div>
            </div>
          )}

          {request.metadata && Object.keys(request.metadata).length > 0 && (
            <div>
              <div className="text-xs text-gray-500 uppercase mb-1">Details</div>
              <pre className="text-xs bg-gray-800 p-2 rounded max-h-32 overflow-auto text-gray-300">
                {JSON.stringify(request.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <div className="flex gap-2 p-4 border-t border-gray-800">
          <button
            onClick={() => onReply(request.id, 'reject')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors"
          >
            Deny
          </button>
          <button
            onClick={() => onReply(request.id, 'allow')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
          >
            Allow Once
          </button>
          <button
            onClick={() => onReply(request.id, 'always')}
            className="flex-1 px-3 py-2 text-sm rounded-lg bg-green-600 hover:bg-green-700 text-white transition-colors"
          >
            Always
          </button>
        </div>
      </div>
    </div>
  )
}
