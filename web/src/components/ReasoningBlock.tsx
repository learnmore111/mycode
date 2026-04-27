import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Lightbulb } from 'lucide-react'

interface Props {
  content: string
  streaming?: boolean
}

export default function ReasoningBlock({ content, streaming = false }: Props) {
  const [collapsed, setCollapsed] = useState(!streaming)
  const wasStreaming = useRef(streaming)

  useEffect(() => {
    if (!wasStreaming.current && streaming) {
      setCollapsed(false)
    }
    if (wasStreaming.current && !streaming) {
      setCollapsed(true)
    }
    wasStreaming.current = streaming
  }, [streaming])

  if (!content) return null

  return (
    <div className="my-3 rounded-lg border border-accent/15 bg-accent/5 overflow-hidden">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center gap-2 w-full px-3 py-2 text-xs text-accent/80 hover:text-accent hover:bg-accent/10 transition-colors text-left"
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        <Lightbulb size={12} />
        <span className="font-medium">思考过程</span>
        {!collapsed && (
          <span className="ml-auto text-xxs text-ink-faint">{content.length} 字符</span>
        )}
      </button>
      {!collapsed && (
        <div className="px-3 pb-3 pt-0 text-sm text-ink-tertiary leading-relaxed whitespace-pre-wrap animate-fade-in">
          {content}
        </div>
      )}
    </div>
  )
}
