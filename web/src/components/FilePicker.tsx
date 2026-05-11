import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X,
  Folder,
  FolderOpen,
  FileCode,
  FileJson,
  FileText,
  File,
  Home,
  Monitor,
  FolderTree,
  ChevronRight,
  Loader2,
  ArrowUp,
  HardDrive,
} from 'lucide-react'
import { apiFetch } from '../api/client'

interface FileEntry {
  name: string
  type: 'file' | 'directory'
  path: string
  size?: number
}

interface BrowseResponse {
  path: string
  parent: string | null
  entries: FileEntry[]
}

interface SystemPaths {
  home?: string
  desktop?: string
  documents?: string
  downloads?: string
}

interface Props {
  onSelectFile?: (path: string) => void
  onSelectDirectory?: (path: string) => void
  mode?: 'file' | 'directory'
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

/* ── Get quick access locations ── */
async function fetchSystemPaths(): Promise<SystemPaths> {
  try {
    return await apiFetch<SystemPaths>('/file/system-paths')
  } catch {
    return {}
  }
}

/* ── Browse a directory ── */
async function browseDir(path: string): Promise<BrowseResponse> {
  return apiFetch<BrowseResponse>(`/file/browse?path=${encodeURIComponent(path)}`)
}

/* ── Breadcrumbs ── */
function Breadcrumbs({
  currentPath,
  onNavigate,
}: {
  currentPath: string
  onNavigate: (path: string) => void
}) {
  const parts = currentPath.split('/').filter(Boolean)
  const isAbsolute = currentPath.startsWith('/')

  return (
    <div className="flex items-center gap-1 px-3 py-2 border-b border-line-subtle overflow-x-auto flex-shrink-0">
      <button
        onClick={() => onNavigate('/')}
        className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors flex-shrink-0"
        title="根目录"
      >
        <HardDrive size={12} />
      </button>
      {isAbsolute &&
        parts.map((part, i) => {
          const buildPath = '/' + parts.slice(0, i + 1).join('/')
          const isLast = i === parts.length - 1
          return (
            <div key={buildPath} className="flex items-center gap-1 flex-shrink-0">
              <ChevronRight size={10} className="text-ink-faint" />
              <button
                onClick={() => onNavigate(buildPath)}
                className={`px-1.5 py-0.5 rounded text-xs transition-colors truncate max-w-[120px] ${
                  isLast
                    ? 'text-ink font-medium bg-surface-2'
                    : 'text-ink-muted hover:bg-surface-hover hover:text-ink-secondary'
                }`}
              >
                {part}
              </button>
            </div>
          )
        })}
    </div>
  )
}

/* ── Main Component ── */
export default function FilePicker({ onSelectFile, onSelectDirectory, mode = 'file', onClose }: Props) {
  const [currentPath, setCurrentPath] = useState('/')
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [parentPath, setParentPath] = useState<string | null>(null)
  const [systemPaths, setSystemPaths] = useState<SystemPaths>({})
  const [searchQuery, setSearchQuery] = useState('')
  const [showSidebar, setShowSidebar] = useState(true)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    fetchSystemPaths().then(setSystemPaths)
  }, [])

  const navigateTo = useCallback(async (path: string) => {
    setLoading(true)
    try {
      const res = await browseDir(path)
      setCurrentPath(res.path)
      setParentPath(res.parent)
      setEntries(res.entries)
      setSearchQuery('')
    } catch {
      // silently fail
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    navigateTo(currentPath)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const filteredEntries = searchQuery
    ? entries.filter((e) => e.name.toLowerCase().includes(searchQuery.toLowerCase()))
    : entries

  const quickLocations = [
    { key: 'home', label: '主目录', icon: <Home size={14} />, path: systemPaths.home },
    { key: 'desktop', label: '桌面', icon: <Monitor size={14} />, path: systemPaths.desktop },
    { key: 'documents', label: '文档', icon: <FolderTree size={14} />, path: systemPaths.documents },
    { key: 'downloads', label: '下载', icon: <FolderOpen size={14} />, path: systemPaths.downloads },
  ].filter((l) => l.path)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-surface-0 border border-line rounded-2xl shadow-2xl overflow-hidden animate-scale-in w-[700px] max-w-[90vw] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-line">
          <FolderOpen size={16} className="text-status-warning flex-shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="在当前目录中筛选..."
            className="flex-1 bg-transparent text-sm text-ink placeholder:text-ink-muted outline-none"
          />
          <button
            onClick={() => setShowSidebar(!showSidebar)}
            className={`p-1.5 rounded-lg transition-colors ${
              showSidebar
                ? 'bg-accent-light text-accent'
                : 'hover:bg-surface-hover text-ink-muted'
            }`}
            title="切换侧边栏"
          >
            <FolderTree size={13} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-1 min-h-0">
          {/* Sidebar - Quick Access */}
          {showSidebar && (
            <div className="w-44 border-r border-line bg-surface-1/50 flex-shrink-0 overflow-y-auto">
              <div className="px-3 py-2">
                <span className="text-xxs text-ink-muted font-medium uppercase tracking-wider">
                  快速访问
                </span>
              </div>
              <div className="px-2 pb-2">
                {quickLocations.map((loc) => (
                  <button
                    key={loc.key}
                    onClick={() => navigateTo(loc.path!)}
                    className={`flex items-center gap-2 w-full px-2.5 py-2 rounded-lg text-sm transition-colors text-left ${
                      currentPath === loc.path
                        ? 'bg-accent-light text-accent'
                        : 'hover:bg-surface-hover text-ink-secondary'
                    }`}
                  >
                    {loc.icon}
                    <span className="truncate">{loc.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Main content */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Breadcrumbs + Parent button */}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-line-subtle flex-shrink-0">
              {parentPath && (
                <button
                  onClick={() => navigateTo(parentPath)}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-2 hover:bg-surface-3 text-xs text-ink-muted hover:text-ink-secondary transition-colors flex-shrink-0"
                >
                  <ArrowUp size={11} />
                  上级
                </button>
              )}
              <div className="flex-1 min-w-0">
                <Breadcrumbs currentPath={currentPath} onNavigate={navigateTo} />
              </div>
            </div>

            {/* File list */}
            <div className="flex-1 overflow-y-auto">
              {loading ? (
                <div className="flex items-center justify-center py-12 gap-2">
                  <Loader2 size={16} className="animate-spin text-accent" />
                  <span className="text-xs text-ink-muted">加载中...</span>
                </div>
              ) : filteredEntries.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 gap-2">
                  <Folder size={20} className="text-ink-faint" />
                  <span className="text-sm text-ink-muted">
                    {searchQuery ? '无匹配文件' : '空目录'}
                  </span>
                </div>
              ) : (
                <div className="py-1">
                  {filteredEntries.map((entry) => (
                    <div key={entry.path} className="group flex items-center">
                      <button
                        onClick={() => {
                          if (entry.type === 'directory') {
                            navigateTo(entry.path)
                            return
                          }
                          onSelectFile?.(entry.path)
                        }}
                        className="flex items-center gap-2.5 flex-1 min-w-0 px-4 py-2 text-sm hover:bg-surface-hover transition-colors text-left"
                      >
                        {entry.type === 'directory' ? (
                          <Folder size={14} className="text-status-warning flex-shrink-0" />
                        ) : (
                          getFileIcon(entry.name)
                        )}
                        <span
                          className={`truncate text-sm ${
                            entry.type === 'file'
                              ? 'group-hover:text-accent transition-colors'
                              : 'font-medium'
                          }`}
                        >
                          {entry.name}
                        </span>
                        {entry.type === 'file' && entry.size != null && (
                          <span className="text-xxs text-ink-faint ml-auto font-mono flex-shrink-0">
                            {entry.size < 1024
                              ? `${entry.size} B`
                              : `${(entry.size / 1024).toFixed(1)} KB`}
                          </span>
                        )}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-line-subtle bg-surface-2/50 flex items-center justify-between">
          <span className="text-xxs text-ink-faint">
            {filteredEntries.length} 个项目
            {searchQuery && ` · 筛选 "${searchQuery}"`}
          </span>
          <div className="flex items-center gap-2">
            {mode === 'directory' && onSelectDirectory && (
              <button
                onClick={() => onSelectDirectory(currentPath)}
                className="px-3 py-1.5 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors"
              >
                打开当前文件夹
              </button>
            )}
            <span className="text-xxs text-ink-faint">
              <kbd className="px-1 py-0.5 bg-surface-0 rounded text-xxs border border-line font-mono">Esc</kbd>
              {' '}关闭
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
