import { useState, useRef, useEffect } from 'react'
import {
  CornerDownLeft,
  Square,
  ChevronDown,
  FolderOpen,
  Cpu,
  Bot,
  Check,
  Search,
  Paperclip,
} from 'lucide-react'
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

/* ── Inline Dropdown (compact) ── */
function ToolbarDropdown({
  items,
  value,
  onChange,
  placeholder,
  icon,
}: {
  items: { id: string; label: string; sub?: string }[]
  value?: string
  onChange?: (v: string | undefined) => void
  placeholder: string
  icon: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    if (open && items.length > 5) {
      setTimeout(() => searchRef.current?.focus(), 50)
    }
    if (!open) setQuery('')
  }, [open, items.length])

  const selected = items.find((i) => i.id === value)
  const filtered = query
    ? items.filter(
        (i) =>
          i.label.toLowerCase().includes(query.toLowerCase()) ||
          i.sub?.toLowerCase().includes(query.toLowerCase())
      )
    : items

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-all ${
          open || value
            ? 'text-accent bg-accent-light'
            : 'text-ink-tertiary bg-surface-2 hover:bg-surface-3 hover:text-ink-secondary'
        }`}
      >
        {icon}
        <span className="font-medium truncate max-w-[100px]">
          {selected ? selected.label.split(' / ').pop() : placeholder}
        </span>
        <ChevronDown
          size={9}
          className={`transition-transform ${open ? 'rotate-180' : ''} opacity-50`}
        />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-2 min-w-[240px] max-w-[320px] bg-surface-0 border border-line rounded-xl shadow-lg z-50 overflow-hidden animate-slide-up">
          {/* Search if many items */}
          {items.length > 5 && (
            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-line-subtle">
              <Search size={12} className="text-ink-muted flex-shrink-0" />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索..."
                className="flex-1 bg-transparent text-xs text-ink placeholder:text-ink-muted outline-none"
              />
            </div>
          )}

          <div className="max-h-56 overflow-y-auto py-1">
            {/* Default option */}
            <button
              onClick={() => {
                onChange?.(undefined)
                setOpen(false)
              }}
              className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs hover:bg-surface-hover transition-colors ${
                !value ? 'text-accent' : 'text-ink-muted'
              }`}
            >
              <span className="w-3.5 h-3.5 flex items-center justify-center">
                {!value && <Check size={11} />}
              </span>
              <span className="font-medium">默认</span>
            </button>

            {filtered.map((item) => (
              <button
                key={item.id}
                onClick={() => {
                  onChange?.(item.id)
                  setOpen(false)
                }}
                className={`flex items-center gap-2.5 w-full px-3 py-2 text-xs hover:bg-surface-hover transition-colors ${
                  item.id === value
                    ? 'text-accent bg-accent-light/50'
                    : 'text-ink-secondary'
                }`}
              >
                <span className="w-3.5 h-3.5 flex items-center justify-center flex-shrink-0">
                  {item.id === value && <Check size={11} className="text-accent" />}
                </span>
                <div className="flex-1 min-w-0 text-left">
                  <div className="font-medium truncate">{item.label}</div>
                  {item.sub && (
                    <div className="text-xxs text-ink-muted truncate mt-0.5">
                      {item.sub}
                    </div>
                  )}
                </div>
              </button>
            ))}

            {filtered.length === 0 && (
              <div className="px-3 py-4 text-xs text-ink-muted text-center">
                无匹配结果
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Main Input Component ── */
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
      el.style.height = Math.min(el.scrollHeight, 180) + 'px'
    }
  }

  // Model items
  const modelItems = models.map((m) => ({
    id: m.id,
    label: m.name,
    sub: m.provider,
  }))

  // Agent items
  const agentItems = agents.map((a) => ({
    id: a.name,
    label: a.name,
    sub: a.description,
  }))

  return (
    <div className="relative max-w-3xl mx-auto w-full">
      {/* File Browser popup */}
      {showFileBrowser && (
        <div className="absolute bottom-full mb-2 left-0 right-0 z-20">
          <FileBrowser
            onSelectFile={(path) => {
              setText((prev) => prev + `@${path} `)
              setShowFileBrowser(false)
              textareaRef.current?.focus()
            }}
            onClose={() => setShowFileBrowser(false)}
          />
        </div>
      )}

      <div className="rounded-2xl border border-line bg-surface-0 shadow-sm transition-all focus-within:border-accent/30 focus-within:shadow-md">
        {/* Text area */}
        <div className="px-4 pt-4 pb-2">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              handleInput()
            }}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题...（Shift+Enter 换行）"
            rows={1}
            disabled={streaming}
            className="w-full bg-transparent resize-none outline-none text-base text-ink placeholder:text-ink-muted max-h-[180px] leading-relaxed"
          />
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between px-3 pb-3 pt-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* Model selector */}
            {models.length > 0 && (
              <ToolbarDropdown
                items={modelItems}
                value={selectedModel}
                onChange={onModelChange}
                placeholder="模型"
                icon={<Cpu size={11} />}
              />
            )}

            {/* Agent selector */}
            {agents.length > 0 && (
              <ToolbarDropdown
                items={agentItems}
                value={selectedAgent}
                onChange={onAgentChange}
                placeholder="智能体"
                icon={<Bot size={11} />}
              />
            )}

            {/* Separator */}
            {(models.length > 0 || agents.length > 0) && (
              <div className="w-px h-4 bg-line mx-0.5" />
            )}

            {/* File browser toggle */}
            <button
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-all ${
                showFileBrowser
                  ? 'text-accent bg-accent-light'
                  : 'text-ink-tertiary bg-surface-2 hover:bg-surface-3 hover:text-ink-secondary'
              }`}
              onClick={() => setShowFileBrowser(!showFileBrowser)}
              title="浏览文件 (添加文件引用)"
            >
              <Paperclip size={11} />
              <span className="font-medium">文件</span>
            </button>
          </div>

          {/* Right side: char count + send */}
          <div className="flex items-center gap-2.5">
            {text.length > 0 && (
              <span className="text-xxs text-ink-faint font-mono tabular-nums">
                {text.length}
              </span>
            )}

            {/* Send / Stop button */}
            <button
              onClick={handleSubmit}
              className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                streaming
                  ? 'bg-status-error text-white hover:bg-status-error/90 shadow-xs'
                  : text.trim()
                  ? 'bg-accent text-white hover:bg-accent-hover shadow-xs hover:shadow-sm'
                  : 'bg-surface-3 text-ink-faint cursor-not-allowed'
              }`}
              disabled={!streaming && !text.trim()}
              title={streaming ? '停止' : '发送 (Enter)'}
            >
              {streaming ? (
                <>
                  <Square size={12} />
                  <span>停止</span>
                </>
              ) : (
                <>
                  <CornerDownLeft size={12} />
                  <span>发送</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
