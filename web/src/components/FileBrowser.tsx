import { useState, useEffect, useRef } from 'react'
import {
  Search,
  X,
  Folder,
  FileCode,
  FileJson,
  FileText,
  File,
  ChevronRight,
  ChevronDown,
  Loader2,
  FolderTree,
} from 'lucide-react'
import { useFileTree, useFileSearch } from '../hooks/useFiles'
import type { FileEntry } from '../api/files'

interface Props {
  onSelectFile: (path: string) => void
  onClose: () => void
}

function getFileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
    case 'py':
    case 'go':
    case 'rs':
    case 'java':
    case 'cpp':
    case 'c':
    case 'css':
    case 'scss':
    case 'vue':
    case 'svelte':
      return <FileCode size={14} className="text-accent-blue flex-shrink-0" />
    case 'json':
    case 'yaml':
    case 'yml':
    case 'toml':
      return <FileJson size={14} className="text-accent-amber flex-shrink-0" />
    case 'md':
    case 'txt':
    case 'rst':
      return <FileText size={14} className="text-text-tertiary flex-shrink-0" />
    default:
      return <File size={14} className="text-text-muted flex-shrink-0" />
  }
}

function FileTreeItem({
  entry,
  depth,
  expanded,
  loadingDirs,
  onToggleDir,
  onSelectFile,
}: {
  entry: FileEntry
  depth: number
  expanded: Record<string, FileEntry[]>
  loadingDirs: Set<string>
  onToggleDir: (path: string) => void
  onSelectFile: (path: string) => void
}) {
  const isDir = entry.type === 'directory'
  const isExpanded = !!expanded[entry.path]
  const isLoading = loadingDirs.has(entry.path)

  return (
    <>
      <button
        onClick={() => isDir ? onToggleDir(entry.path) : onSelectFile(entry.path)}
        className="flex items-center gap-2 w-full px-3 py-1.5 text-sm hover:bg-surface-2 transition-colors text-left"
        style={{ paddingLeft: `${depth * 16 + 12}px` }}
      >
        {isDir ? (
          isLoading ? (
            <Loader2 size={12} className="text-text-muted animate-spin flex-shrink-0" />
          ) : isExpanded ? (
            <ChevronDown size={12} className="text-text-muted flex-shrink-0" />
          ) : (
            <ChevronRight size={12} className="text-text-muted flex-shrink-0" />
          )
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}
        {isDir ? (
          <Folder size={14} className="text-accent-amber flex-shrink-0" />
        ) : (
          getFileIcon(entry.name)
        )}
        <span className={isDir ? 'text-text-secondary font-medium' : 'text-text-primary'}>
          {entry.name}
        </span>
      </button>
      {isDir && isExpanded && expanded[entry.path]?.map((child) => (
        <FileTreeItem
          key={child.path}
          entry={child}
          depth={depth + 1}
          expanded={expanded}
          loadingDirs={loadingDirs}
          onToggleDir={onToggleDir}
          onSelectFile={onSelectFile}
        />
      ))}
    </>
  )
}

export default function FileBrowser({ onSelectFile, onClose }: Props) {
  const [mode, setMode] = useState<'search' | 'tree'>('search')
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const tree = useFileTree()
  const fileSearch = useFileSearch()

  // Focus search input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Load tree when switching to tree mode
  useEffect(() => {
    if (mode === 'tree' && tree.rootEntries.length === 0) {
      tree.loadRoot()
    }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Search as user types
  useEffect(() => {
    if (mode === 'search') {
      fileSearch.search(query)
    }
  }, [query, mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      ref={containerRef}
      className="bg-surface-1 border border-border rounded-lg shadow-elevated overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle">
        <Search size={14} className="text-text-muted flex-shrink-0" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索文件..."
          className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none"
        />
        <button
          onClick={() => setMode(mode === 'search' ? 'tree' : 'search')}
          className={`p-1 rounded hover:bg-surface-2 transition-colors ${
            mode === 'tree' ? 'text-accent-blue' : 'text-text-muted'
          }`}
          title={mode === 'search' ? '切换到树形视图' : '切换到搜索'}
        >
          <FolderTree size={14} />
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-surface-2 text-text-muted hover:text-text-secondary transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className="max-h-80 overflow-y-auto">
        {mode === 'search' ? (
          // Search results
          <>
            {fileSearch.searching && (
              <div className="flex items-center justify-center py-4">
                <Loader2 size={16} className="animate-spin text-text-muted" />
              </div>
            )}
            {!fileSearch.searching && query && fileSearch.results.length === 0 && (
              <div className="text-center py-4 text-text-muted text-sm">
                未找到匹配文件
              </div>
            )}
            {!query && !fileSearch.searching && (
              <div className="text-center py-4 text-text-muted text-sm">
                输入文件名开始搜索
              </div>
            )}
            {fileSearch.results.map((path) => {
              const parts = path.split('/')
              const name = parts.pop() || path
              const dir = parts.join('/')
              return (
                <button
                  key={path}
                  onClick={() => onSelectFile(path)}
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-surface-2 transition-colors text-left"
                >
                  {getFileIcon(name)}
                  <span className="text-text-primary truncate">{name}</span>
                  {dir && <span className="text-text-muted text-xs truncate ml-auto">{dir}/</span>}
                </button>
              )
            })}
          </>
        ) : (
          // Tree view
          <>
            {tree.loading && (
              <div className="flex items-center justify-center py-4">
                <Loader2 size={16} className="animate-spin text-text-muted" />
              </div>
            )}
            {!tree.loading && tree.rootEntries.length === 0 && (
              <div className="text-center py-4 text-text-muted text-sm">
                无法加载项目文件
              </div>
            )}
            {tree.rootEntries.map((entry) => (
              <FileTreeItem
                key={entry.path}
                entry={entry}
                depth={0}
                expanded={tree.expanded}
                loadingDirs={tree.loadingDirs}
                onToggleDir={tree.toggleDir}
                onSelectFile={onSelectFile}
              />
            ))}
          </>
        )}
      </div>
    </div>
  )
}
