import { useState, useRef, useEffect } from 'react'
import { Send, Square } from 'lucide-react'

interface Props {
  onSend: (text: string) => void
  onAbort: () => void
  streaming: boolean
}

export default function MessageInput({ onSend, onAbort, streaming }: Props) {
  const [text, setText] = useState('')
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
    // Reset textarea height
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
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }

  return (
    <div className="p-4 border-t border-gray-800">
      <div className="flex items-end gap-2 bg-gray-900 rounded-xl border border-gray-700 focus-within:border-blue-500 transition-colors px-3 py-2">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            handleInput()
          }}
          onKeyDown={handleKeyDown}
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          rows={1}
          disabled={streaming}
          className="flex-1 bg-transparent resize-none outline-none text-sm text-gray-100 placeholder-gray-500 max-h-[200px]"
        />
        <button
          onClick={handleSubmit}
          className={`flex-shrink-0 p-2 rounded-lg transition-colors ${
            streaming
              ? 'bg-red-600 hover:bg-red-700 text-white'
              : text.trim()
              ? 'bg-blue-600 hover:bg-blue-700 text-white'
              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
          }`}
          disabled={!streaming && !text.trim()}
        >
          {streaming ? <Square size={16} /> : <Send size={16} />}
        </button>
      </div>
    </div>
  )
}
