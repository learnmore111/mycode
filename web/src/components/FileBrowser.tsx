import { useState, useEffect, useRef } from 'react'
import {
  Search,
  X,
  Folder,
  FolderOpen,
  FileCode,
  FileJson,
  FileText,
  File,
  ChevronRight,
  Loader2,
  FolderTree,
  Hash,
} from 'lucide-react'
import { useFileTree, useFileSearch } from '../hooks/useFiles'
import type { FileEntry } from '../api/files'

interface Props {
  onSelectFile: (path: string) => void
  onClose: () => void
}

/* ── File icon with color coding ── */
function getFileIcon(name: string, size = 14) {
  const ext = name.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'ts':
    case 'tsx':
      return <FileCode size={size} className="text-status-info flex-shrink-0" />
    case 'js':
    case 'jsx':
      return <FileCode size={size} className="text-status-warning flex-shrink-0" />
    case 'py':
      return <FileCode size={size} className="text-accent flex-shrink-0" />
    case 'go':
    case 'rs':
    case 'java':
    case 'cpp':
    case 'c':
      return <FileCode size={size} className="text-status-success flex-shrink-0" />
    case 'css':
    case 'scss':
    case 'vue':
    case 'svelte':
      return <FileCode size={size} className="text-status-error flex-shrink-0" />
    case 'json':
    case 'yaml':
    case 'yml':
    case 'toml':
      return <FileJson size={size} className="text-status-warning flex-shrink-0" />
    case 'md':
    case 'txt':
    case 'rst':
      return <FileText size={size} className="text-ink-tertiary flex-shrink-0" />
    default:
      return <File size={size} className="text-ink-muted flex-shrink-0" />
  }
}

/* ── Tree Item ── */
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
        onClick={() => (isDir ? onToggleDir(entry.path) : onSelectFile(entry.path))}
        className={`flex items-center gap-2 w-full py-1.5 text-sm transition-colors text-left group ${
          isDir
            ? 'hover:bg-surface-hover text-ink-secondary'
            : 'hover:bg-accent-light text-ink hover:text-accent'
        }`}
        style={{ paddingLeft: `${depth * 16 + 12}px`, paddingRight: '12px' }}
      >
        {isDir ? (
          isLoading ? (
            <Loader2 size={12} className="text-accent animate-spin flex-shrink-0" />
          ) : (
            <ChevronRight
              size={12}
              className={`text-ink-muted flex-shrink-0 transition-transform ${
                isExpanded ? 'rotate-90' : ''
              }`}
            />
          )
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}
        {isDir ? (
          isExpanded ? (
            <FolderOpen size={14} className="text-status-warning flex-shrink-0" />
          ) : (
            <Folder size={14} className="text-status-warning flex-shrink-0" />
          )
        ) : (
          getFileIcon(entry.name)
        )}
        <span
          className={`text-sm truncate ${
            isDir ? 'font-medium' : 'group-hover:font-medium'
          }`}
        >
          {entry.name}
        </span>
        {isDir && isExpanded && expanded[entry.path] && (
          <span className="text-xxs text-ink-faint font-mono ml-auto">
            {expanded[entry.path].length}
          </span>
        )}
      </button>
      {isDir &&
        isExpanded &&
        expanded[entry.path]?.map((child) => (
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

/* ── Main Component ── */
export default function FileBrowser({ onSelectFile, onClose }: Props) {
  const [mode, setMode] = useState<'search' | 'tree'>('search')
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const tree = useFileTree()
  const fileSearch = useFileSearch()

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (mode === 'tree' && tree.rootEntries.length === 0) {
      tree.loadRoot()
    }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode === 'search') {
      fileSearch.search(query)
    }
  }, [query, mode]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

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
      className="bg-surface-0 border border-line rounded-2xl shadow-lg overflow-hidden animate-slide-up"
    >
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-line">
        <Search size={14} className="text-ink-muted flex-shrink-0" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={mode === 'search' ? '搜索文件名...' : '浏览项目文件'}
          className="flex-1 bg-transparent text-sm text-ink placeholder:text-ink-muted outline-none"
        />

        {/* Mode toggle */}
        <div className="flex items-center bg-surface-2 rounded-lg p-0.5">
          <button
            onClick={() => setMode('search')}
            className={`px-2 py-1 rounded-md text-xxs font-medium transition-all ${
              mode === 'search'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            搜索
          </button>
          <button
            onClick={() => setMode('tree')}
            className={`px-2 py-1 rounded-md text-xxs font-medium transition-all ${
              mode === 'tree'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            <FolderTree size={11} className="inline-block mr-1" />
            树形
          </button>
        </div>

        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
        >
          <X size={13} />
        </button>
      </div>

      {/* Content */}
      <div className="max-h-80 overflow-y-auto">
        {mode === 'search' ? (
          <>
            {fileSearch.searching && (
              <div className="flex items-center justify-center py-8 gap-2">
                <Loader2 size={16} className="animate-spin text-accent" />
                <span className="text-xs text-ink-muted">搜索中...</span>
              </div>
            )}
            {!fileSearch.searching && query && fileSearch.results.length === 0 && (
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Search size={18} className="text-ink-faint" />
                <span className="text-sm text-ink-muted">无匹配结果</span>
                <span className="text-xs text-ink-faint">尝试其他关键词</span>
              </div>
            )}
            {!query && !fileSearch.searching && (
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Hash size={18} className="text-ink-faint" />
                <span className="text-sm text-ink-muted">输入文件名搜索</span>
                <span className="text-xs text-ink-faint">选择文件后会以 @路径 形式插入</span>
              </div>
            )}
            {query && fileSearch.results.length > 0 && (
              <div className="px-3 py-1.5 border-b border-line-subtle">
                <span className="text-xxs text-ink-muted font-medium">
                  找到 {fileSearch.results.length} 个文件
                </span>
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
                  className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm hover:bg-accent-light transition-colors text-left group"
                >
                  {getFileIcon(name)}
                  <span className="text-ink font-medium truncate group-hover:text-accent transition-colors">
                    {name}
                  </span>
                  {dir && (
                    <span className="text-ink-faint text-xxs truncate ml-auto font-mono">
                      {dir}/
                    </span>
                  )}
                </button>
              )
            })}
          </>
        ) : (
          <>
            {tree.loading && (
              <div className="flex items-center justify-center py-8 gap-2">
                <Loader2 size={16} className="animate-spin text-accent" />
                <span className="text-xs text-ink-muted">加载文件树...</span>
              </div>
            )}
            {!tree.loading && tree.rootEntries.length === 0 && (
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Folder size={18} className="text-ink-faint" />
                <span className="text-sm text-ink-muted">无法加载文件</span>
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

      {/* Footer hint */}
      <div className="px-4 py-2 border-t border-line-subtle bg-surface-2/50">
        <span className="text-xxs text-ink-faint">
          <kbd className="px-1 py-0.5 bg-surface-0 rounded text-xxs border border-line font-mono">Esc</kbd>
          {' '}关闭
          {mode === 'search' && ' · 输入文件名过滤'}
        </span>
      </div>
    </div>
  )
}
