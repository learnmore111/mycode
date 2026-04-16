import { useState, useRef, useEffect } from 'react'
import { CornerDownLeft, Square, Globe, FolderOpen, Bot, ChevronDown } from 'lucide-react'
import type { AgentInfo } from '../types'
import FileBrowser from './FileBrowser'

interface Props {
  onSend: (text: string) => void
  onAbort: () => void
  streaming: boolean
  models?: { id: string; name: string; provider: string }[]
  agents?: AgentInfo[]
  selectedModel?: string
  selectedAgent?: string
  onModelChange?: (m: string | undefined) => void
  onAgentChange?: (a: string | undefined) => void
}

export default function MessageInput({
  onSend,
  onAbort,
  streaming,
  models = [],
  agents = [],
  selectedModel,
  selectedAgent,
  onModelChange,
  onAgentChange,
}: Props) {
  const [text, setText] = useState('')
  const [showFileBrowser, setShowFileBrowser] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const handleSubmit = () => {
    if (streaming) {
      onAbort()
      return
    }
    if (!text.trim()) return
    onSend(text.trim())
    setText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 160) + 'px'
    }
  }

  return (
    <div className="relative max-w-3xl mx-auto w-full">
      {/* File Browser popup */}
      {showFileBrowser && (
        <div className="absolute bottom-full mb-2 left-0 right-0 z-20">
          <FileBrowser
            onSelectFile={(path) => {
              setText(prev => prev + `@${path} `)
              setShowFileBrowser(false)
              textareaRef.current?.focus()
            }}
            onClose={() => setShowFileBrowser(false)}
          />
        </div>
      )}

      <div className="input-bar rounded-xl">
        {/* Text area */}
        <div className="px-4 pt-3 pb-1">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            placeholder="描述你想要创建的项目，或输入任何问题..."
            rows={1}
            disabled={streaming}
            className="w-full bg-transparent resize-none outline-none text-sm text-text-primary placeholder:text-text-muted max-h-[160px] leading-relaxed"
          />
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between px-3 pb-2.5">
          <div className="flex items-center gap-1 overflow-x-auto">
            {/* Model selector */}
            {models.length > 0 && (
              <div className="relative">
                <select
                  value={selectedModel ?? ''}
                  onChange={(e) => onModelChange?.(e.target.value || undefined)}
                  className="toolbar-select flex items-center gap-1 pr-5"
                >
                  <option value="">默认模型</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
                <Globe size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
              </div>
            )}

            {/* Agent selector */}
            {agents.length > 0 && (
              <div className="relative">
                <select
                  value={selectedAgent ?? ''}
                  onChange={(e) => onAgentChange?.(e.target.value || undefined)}
                  className="toolbar-select pl-6"
                >
                  <option value="">默认 Agent</option>
                  {agents.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <Bot size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
              </div>
            )}

            {/* Project folder button */}
            <button
              className={`toolbar-btn ${showFileBrowser ? 'active' : ''}`}
              onClick={() => setShowFileBrowser(!showFileBrowser)}
              title="浏览项目文件"
            >
              <FolderOpen size={12} />
              <span>当前项目</span>
            </button>
          </div>

          {/* Send button */}
          <button
            onClick={handleSubmit}
            className={`flex-shrink-0 p-2.5 rounded-lg transition-all ${
              streaming
                ? 'bg-accent-red hover:bg-red-500 text-white shadow-card'
                : text.trim()
                ? 'bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-400 hover:to-purple-400 text-white shadow-card hover:shadow-glow-purple hover:scale-[1.02]'
                : 'text-text-muted cursor-not-allowed'
            }`}
            disabled={!streaming && !text.trim()}
            title={streaming ? '停止' : '发送 (Enter)'}
          >
            {streaming ? <Square size={16} /> : <CornerDownLeft size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
