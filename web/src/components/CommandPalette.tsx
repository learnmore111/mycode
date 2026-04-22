import { useEffect, useMemo, useRef, useState } from 'react'
import type { Session } from '../types'

export interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  sessions: Session[]
  activeId: string | null
  onSelect: (sessionId: string) => void
}

/**
 * Minimal command palette — opens via Cmd+K / Ctrl+K and lets the user
 * fuzzy-filter sessions by title or slug, pick with Enter, or navigate
 * with arrow keys. Does not depend on any external UI library; all
 * styling uses the app's existing Tailwind tokens so it slots in
 * alongside the dark theme without extra CSS.
 */
export default function CommandPalette({
  open,
  onClose,
  sessions,
  activeId,
  onSelect,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sessions.slice(0, 50)
    return sessions
      .filter((s) => {
        const hay = `${s.title} ${s.slug} ${s.id}`.toLowerCase()
        return hay.includes(q)
      })
      .slice(0, 50)
  }, [query, sessions])

  // Reset state whenever the palette opens so the user starts with a
  // clean query + the first result highlighted.
  useEffect(() => {
    if (!open) return
    setQuery('')
    setCursor(0)
    // Autofocus on next tick so the input element has mounted.
    const t = setTimeout(() => inputRef.current?.focus(), 0)
    return () => clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (cursor >= results.length) setCursor(Math.max(0, results.length - 1))
  }, [cursor, results.length])

  if (!open) return null

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => Math.min(results.length - 1, c + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => Math.max(0, c - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const picked = results[cursor]
      if (picked) {
        onSelect(picked.id)
        onClose()
      }
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[12vh]"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-xl rounded-lg border border-gray-700 bg-gray-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div className="border-b border-gray-800 px-3 py-2">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Jump to session…  (↑ ↓ to move, Enter to open, Esc to close)"
            className="w-full bg-transparent text-sm text-gray-100 placeholder-gray-500 outline-none"
            aria-label="Search sessions"
            aria-autocomplete="list"
            aria-controls="command-palette-results"
          />
        </div>
        <ul
          id="command-palette-results"
          role="listbox"
          aria-label="Session results"
          className="max-h-80 overflow-y-auto py-1"
        >
          {results.length === 0 && (
            <li className="px-3 py-2 text-sm text-gray-500">No sessions match.</li>
          )}
          {results.map((s, idx) => {
            const selected = idx === cursor
            const isActive = s.id === activeId
            return (
              <li
                key={s.id}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setCursor(idx)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  onSelect(s.id)
                  onClose()
                }}
                className={
                  'cursor-pointer px-3 py-2 text-sm ' +
                  (selected ? 'bg-blue-600 text-white' : 'text-gray-200 hover:bg-gray-800')
                }
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate">{s.title || s.slug || s.id}</span>
                  {isActive && (
                    <span className="text-xs uppercase tracking-wide text-emerald-400">
                      current
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-500 truncate">{s.id}</div>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
