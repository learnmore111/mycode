import { useState, useCallback, useRef } from 'react'
import { listDir, searchFiles, type FileEntry } from '../api/files'

interface ExpandedDirs {
  [path: string]: FileEntry[]
}

export function useFileTree() {
  const [rootEntries, setRootEntries] = useState<FileEntry[]>([])
  const [expanded, setExpanded] = useState<ExpandedDirs>({})
  const [loading, setLoading] = useState(false)
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set())

  const loadRoot = useCallback(async () => {
    setLoading(true)
    try {
      const entries = await listDir()
      setRootEntries(entries)
    } catch {
      // silently fail
    } finally {
      setLoading(false)
    }
  }, [])

  const toggleDir = useCallback(async (path: string) => {
    if (expanded[path]) {
      setExpanded(prev => {
        const next = { ...prev }
        delete next[path]
        return next
      })
      return
    }
    setLoadingDirs(prev => new Set(prev).add(path))
    try {
      const entries = await listDir(path)
      setExpanded(prev => ({ ...prev, [path]: entries }))
    } catch {
      // silently fail
    } finally {
      setLoadingDirs(prev => {
        const next = new Set(prev)
        next.delete(path)
        return next
      })
    }
  }, [expanded])

  return { rootEntries, expanded, loading, loadingDirs, loadRoot, toggleDir }
}

export function useFileSearch() {
  const [results, setResults] = useState<string[]>([])
  const [searching, setSearching] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  const search = useCallback((query: string) => {
    clearTimeout(debounceRef.current)
    if (!query.trim()) {
      setResults([])
      setSearching(false)
      return
    }
    setSearching(true)
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await searchFiles(query, 20)
        setResults(res)
      } catch {
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 200)
  }, [])

  const clearSearch = useCallback(() => {
    setResults([])
    setSearching(false)
  }, [])

  return { results, searching, search, clearSearch }
}
