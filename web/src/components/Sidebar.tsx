import { useState, useMemo } from 'react'
import {
  Plus,
  Trash2,
  RotateCcw,
  MessageSquare,
  Loader2,
  PanelLeftClose,
  PanelLeft,
  ChevronDown,
  ChevronRight,
  Search,
  X,
  Clock,
  CalendarDays,
  GitBranch,
  Wand2,
  Plug,
} from 'lucide-react'
import GitSidebar from './GitSidebar'
import SkillsSidebar from './SkillsSidebar'
import McpSidebar from './McpSidebar'
import type { GitStatus, Session } from '../types'
import type { SkillInfo } from '../api/skills'
import type { McpStatus } from '../api/mcp'
import { getSessionSearchText, getSessionSummaryBadges } from '../utils/sessionInsights'

interface Props {
  sessions: Session[]
  deletedSessions: Session[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  onRestore: (id: string) => void
  loading: boolean
  gitStatus: GitStatus | null
  gitLoading: boolean
  gitError: string | null
  selectedGitPath: string | null
  onSelectGitFile: (path: string) => void
  onRefreshGit: () => void
  width?: number
  skills: SkillInfo[]
  skillsLoading: boolean
  onRefreshSkills: () => void
  mcpStatus: McpStatus | null
  mcpLoading: boolean
  onRefreshMcp: () => void
}

function getTimeGroup(timestamp: number): string {
  const now = new Date()
  const ts = timestamp < 1e12 ? timestamp * 1000 : timestamp
  const date = new Date(ts)
  const diffDays = Math.floor((now.getTime() - date.getTime()) / 86400000)

  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays <= 7) return '最近 7 天'
  if (diffDays <= 30) return '最近 30 天'
  return '更早'
}

function groupSessions(sessions: Session[]) {
  const groups: Record<string, Session[]> = {}
  const order = ['今天', '昨天', '最近 7 天', '最近 30 天', '更早']

  for (const s of sessions) {
    const group = getTimeGroup(s.time.created)
    if (!groups[group]) groups[group] = []
    groups[group].push(s)
  }

  return order.filter((g) => groups[g]?.length).map((g) => ({ label: g, sessions: groups[g] }))
}

function formatSessionTime(timestamp: number): string {
  const ts = timestamp < 1e12 ? timestamp * 1000 : timestamp
  const date = new Date(ts)
  const now = new Date()
  const diffDays = Math.floor((now.getTime() - date.getTime()) / 86400000)

  if (diffDays === 0) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

function SessionItem({
  session,
  isActive,
  onSelect,
  onDelete,
}: {
  session: Session
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const summaryBadges = getSessionSummaryBadges(session.summary)

  return (
    <div
      onClick={onSelect}
      className={`group flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition-all relative ${
        isActive
          ? 'bg-accent-light text-accent shadow-xs'
          : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
      }`}
    >
      {isActive && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-accent" />}

      <MessageSquare size={14} className={`flex-shrink-0 ${isActive ? 'text-accent' : 'text-ink-muted'}`} />
      <div className="flex-1 min-w-0">
        <div className={`text-sm truncate ${isActive ? 'font-semibold' : ''}`}>{session.title || '未命名会话'}</div>
        <div className="text-xxs text-ink-muted mt-0.5 flex items-center gap-1 flex-wrap">
          <span className="flex items-center gap-1">
            <Clock size={9} />
            <span>{formatSessionTime(session.time.created)}</span>
          </span>
          {summaryBadges.map((badge) => (
            <span
              key={badge}
              className={`px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${
                isActive
                  ? 'border-accent/20 bg-white/70 text-accent'
                  : 'border-line-subtle bg-surface-1 text-ink-muted'
              }`}
            >
              {badge}
            </span>
          ))}
        </div>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
        className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-status-error-light text-ink-muted hover:text-status-error transition-all"
        title="删除"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

export default function Sidebar({
  sessions,
  deletedSessions,
  activeId,
  onSelect,
  onCreate,
  onDelete,
  onRestore,
  loading,
  gitStatus,
  gitLoading,
  gitError,
  selectedGitPath,
  onSelectGitFile,
  onRefreshGit,
  width = 256,
  skills,
  skillsLoading,
  onRefreshSkills,
  mcpStatus,
  mcpLoading,
  onRefreshMcp,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [showDeleted, setShowDeleted] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const [activeTab, setActiveTab] = useState<'sessions' | 'git' | 'skills' | 'mcp'>('sessions')

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions
    const q = searchQuery.toLowerCase()
    return sessions.filter((s) => {
      const titleText = (s.title || '').toLowerCase()
      const summaryText = getSessionSearchText(s.summary).toLowerCase()
      return titleText.includes(q) || summaryText.includes(q)
    })
  }, [sessions, searchQuery])

  const grouped = useMemo(() => groupSessions(filteredSessions), [filteredSessions])
  const collapsedCount = activeTab === 'git' ? gitStatus?.summary.changed ?? 0 : sessions.length

  if (collapsed) {
    return (
      <div className="flex flex-col items-center py-4 px-2 gap-2 bg-surface-0 border-r border-line">
        <button
          onClick={() => setCollapsed(false)}
          className="p-2 rounded-lg hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
          title="展开侧边栏"
        >
          <PanelLeft size={16} />
        </button>
        <button
          onClick={() => setActiveTab('sessions')}
          className={`p-2 rounded-lg transition-colors ${
            activeTab === 'sessions'
              ? 'bg-accent-light text-accent'
              : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
          }`}
          title="会话"
        >
          <MessageSquare size={15} />
        </button>
        <button
          onClick={() => setActiveTab('git')}
          className={`p-2 rounded-lg transition-colors ${
            activeTab === 'git'
              ? 'bg-accent-light text-accent'
              : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
          }`}
          title="Git"
        >
          <GitBranch size={15} />
        </button>
        <button
          onClick={() => setActiveTab('skills')}
          className={`p-2 rounded-lg transition-colors ${
            activeTab === 'skills'
              ? 'bg-accent-light text-accent'
              : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
          }`}
          title="技能"
        >
          <Wand2 size={15} />
        </button>
        <button
          onClick={() => setActiveTab('mcp')}
          className={`p-2 rounded-lg transition-colors ${
            activeTab === 'mcp'
              ? 'bg-accent-light text-accent'
              : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
          }`}
          title="MCP"
        >
          <Plug size={15} />
        </button>
        <button
          onClick={onCreate}
          className="p-2 rounded-lg bg-accent text-white hover:bg-accent-hover shadow-xs transition-all"
          title="新建会话"
        >
          <Plus size={16} />
        </button>
        {collapsedCount > 0 && (
          <div className="mt-1 w-6 h-6 rounded-full bg-surface-2 flex items-center justify-center">
            <span className="text-xxs text-ink-muted font-mono font-semibold">{collapsedCount}</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <aside style={{ width }} className="flex-shrink-0 flex flex-col border-r border-line bg-surface-0">
      <div className="px-4 py-4 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center shadow-xs">
            <span className="text-white text-xs font-bold font-mono">M</span>
          </div>
          <div>
            <span className="text-sm font-bold text-ink-strong tracking-tight block leading-tight">MyCode</span>
            <span className="text-xxs text-ink-muted">AI 编程助手</span>
          </div>
        </div>
        <div className="flex items-center gap-0.5">
          {activeTab === 'sessions' && (
            <button
              onClick={() => setIsSearching(!isSearching)}
              className={`p-1.5 rounded-lg transition-colors ${
                isSearching
                  ? 'bg-accent-light text-accent'
                  : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
              }`}
              title="搜索会话"
            >
              <Search size={14} />
            </button>
          )}
          <button
            onClick={() => setCollapsed(true)}
            className="p-1.5 rounded-lg hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors"
            title="折叠侧边栏"
          >
            <PanelLeftClose size={14} />
          </button>
        </div>
      </div>

      <div className="px-3 pb-3">
        <div className="grid grid-cols-4 gap-1 rounded-xl bg-surface-2 p-1">
          <button
            onClick={() => setActiveTab('sessions')}
            className={`flex items-center justify-center gap-1 rounded-lg px-2 py-2 text-xxs font-medium transition-all ${
              activeTab === 'sessions'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            <MessageSquare size={11} />
            <span>会话</span>
          </button>
          <button
            onClick={() => setActiveTab('git')}
            className={`flex items-center justify-center gap-1 rounded-lg px-2 py-2 text-xxs font-medium transition-all ${
              activeTab === 'git'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            <GitBranch size={11} />
            <span>Git</span>
          </button>
          <button
            onClick={() => setActiveTab('skills')}
            className={`flex items-center justify-center gap-1 rounded-lg px-2 py-2 text-xxs font-medium transition-all ${
              activeTab === 'skills'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            <Wand2 size={11} />
            <span>技能</span>
          </button>
          <button
            onClick={() => setActiveTab('mcp')}
            className={`flex items-center justify-center gap-1 rounded-lg px-2 py-2 text-xxs font-medium transition-all ${
              activeTab === 'mcp'
                ? 'bg-surface-0 text-accent shadow-xs'
                : 'text-ink-muted hover:text-ink-secondary'
            }`}
          >
            <Plug size={11} />
            <span>MCP</span>
          </button>
        </div>
      </div>

      {activeTab === 'sessions' ? (
        <>
          {isSearching && (
            <div className="px-3 pb-3 animate-slide-up">
              <div className="flex items-center gap-2 bg-surface-2 rounded-lg px-3 py-2 border border-line-subtle focus-within:border-accent/30 focus-within:bg-surface-0 transition-all">
                <Search size={12} className="text-ink-muted flex-shrink-0" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="搜索标题或改动文件..."
                  className="flex-1 bg-transparent text-xs text-ink placeholder:text-ink-muted outline-none"
                  autoFocus
                />
                {searchQuery && (
                  <button onClick={() => setSearchQuery('')} className="p-0.5 rounded hover:bg-surface-3 text-ink-muted">
                    <X size={11} />
                  </button>
                )}
              </div>
              {searchQuery && (
                <div className="mt-1.5 text-xxs text-ink-muted px-1">找到 {filteredSessions.length} 个会话</div>
              )}
            </div>
          )}

          <div className="px-3 pb-3">
            <button
              onClick={onCreate}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl bg-accent text-white text-sm font-medium hover:bg-accent-hover shadow-xs hover:shadow-sm transition-all"
            >
              <Plus size={14} />
              <span>新建会话</span>
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-2 pb-2">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-12 gap-3">
                <Loader2 size={18} className="animate-spin text-accent" />
                <span className="text-xs text-ink-muted">加载中...</span>
              </div>
            ) : sessions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 gap-3">
                <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
                  <MessageSquare size={20} className="text-ink-faint" />
                </div>
                <div className="text-center">
                  <span className="text-sm text-ink-muted block">暂无会话</span>
                  <span className="text-xs text-ink-faint mt-1 block">点击上方按钮创建</span>
                </div>
              </div>
            ) : filteredSessions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Search size={18} className="text-ink-faint" />
                <span className="text-xs text-ink-muted">未找到匹配的会话</span>
              </div>
            ) : (
              <div className="space-y-4">
                {grouped.map((group) => (
                  <div key={group.label}>
                    <div className="flex items-center gap-2 px-3 py-1.5 mb-1">
                      <CalendarDays size={10} className="text-ink-faint" />
                      <span className="text-xxs font-semibold text-ink-muted uppercase tracking-wider">{group.label}</span>
                      <div className="flex-1 h-px bg-line-subtle" />
                      <span className="text-xxs text-ink-faint font-mono">{group.sessions.length}</span>
                    </div>

                    <div className="space-y-0.5">
                      {group.sessions.map((s) => (
                        <SessionItem
                          key={s.id}
                          session={s}
                          isActive={s.id === activeId}
                          onSelect={() => onSelect(s.id)}
                          onDelete={() => onDelete(s.id)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {deletedSessions.length > 0 && (
              <div className="mt-4 pt-3 border-t border-line-subtle">
                <button
                  onClick={() => setShowDeleted(!showDeleted)}
                  className="flex items-center gap-2 px-3 py-2 w-full text-left rounded-lg hover:bg-surface-hover transition-colors"
                >
                  {showDeleted ? (
                    <ChevronDown size={12} className="text-ink-muted" />
                  ) : (
                    <ChevronRight size={12} className="text-ink-muted" />
                  )}
                  <Trash2 size={12} className="text-ink-muted" />
                  <span className="text-xs text-ink-muted font-medium flex-1">已删除</span>
                  <span className="text-xxs text-ink-faint bg-surface-2 px-1.5 py-0.5 rounded-md font-mono">
                    {deletedSessions.length}
                  </span>
                </button>

                {showDeleted && (
                  <div className="mt-1 space-y-0.5 animate-slide-up">
                    {deletedSessions.map((s) => (
                      <div
                        key={s.id}
                        className="group flex items-center gap-2.5 px-3 py-2 rounded-lg text-ink-muted hover:bg-surface-hover transition-colors"
                      >
                        <MessageSquare size={12} className="flex-shrink-0 opacity-30" />
                        <div className="flex-1 min-w-0">
                          <span className="text-xs opacity-60 truncate block">{s.title || '未命名会话'}</span>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            onRestore(s.id)
                          }}
                          className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-status-success-light text-ink-muted hover:text-status-success transition-all"
                          title="恢复"
                        >
                          <RotateCcw size={12} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="px-4 py-3 border-t border-line-subtle">
            <div className="flex items-center justify-between text-xxs text-ink-faint">
              <span>共 {sessions.length} 个会话</span>
              <span className="font-mono">v0.1</span>
            </div>
          </div>
        </>
      ) : activeTab === 'git' ? (
        <GitSidebar
          status={gitStatus}
          loading={gitLoading}
          error={gitError}
          selectedPath={selectedGitPath}
          onSelectFile={onSelectGitFile}
          onRefresh={onRefreshGit}
        />
      ) : activeTab === 'skills' ? (
        <SkillsSidebar
          skills={skills}
          loading={skillsLoading}
          onRefresh={onRefreshSkills}
        />
      ) : activeTab === 'mcp' ? (
        <McpSidebar
          status={mcpStatus}
          loading={mcpLoading}
          onRefresh={onRefreshMcp}
        />
      ) : null}
    </aside>
  )
}
