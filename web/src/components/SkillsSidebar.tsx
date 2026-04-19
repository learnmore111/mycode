import { useState } from 'react'
import { RefreshCcw, Trash2, Wand2, ChevronDown, ChevronRight, FileText } from 'lucide-react'
import { getSkill, deleteSkill } from '../api/skills'
import type { SkillInfo } from '../api/skills'

interface Props {
  skills: SkillInfo[]
  loading: boolean
  onRefresh: () => void
}

export default function SkillsSidebar({ skills, loading, onRefresh }: Props) {
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)
  const [skillContent, setSkillContent] = useState<string | null>(null)
  const [contentLoading, setContentLoading] = useState(false)

  const toggleSkill = async (name: string) => {
    if (expandedSkill === name) {
      setExpandedSkill(null)
      setSkillContent(null)
      return
    }
    setExpandedSkill(name)
    setContentLoading(true)
    try {
      const detail = await getSkill(name)
      setSkillContent(detail.content)
    } catch {
      setSkillContent('加载失败')
    } finally {
      setContentLoading(false)
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`确定要删除技能 "${name}"？`)) return
    try {
      await deleteSkill(name)
      onRefresh()
      if (expandedSkill === name) {
        setExpandedSkill(null)
        setSkillContent(null)
      }
    } catch (err) {
      console.error('Delete skill failed', err)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4 py-10">
        <RefreshCcw size={16} className="animate-spin text-accent" />
        <span className="text-xs text-ink-muted">加载技能列表...</span>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 pb-3">
        <div className="rounded-2xl border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
              <Wand2 size={14} className="text-accent" />
              <span>技能文件</span>
            </div>
            <button
              onClick={onRefresh}
              className="p-2 rounded-xl text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              title="刷新"
            >
              <RefreshCcw size={13} />
            </button>
          </div>
          <div className="mt-1 text-[11px] text-ink-muted">
            共 {skills.length} 个技能 · .mycode/skills/
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {skills.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
            <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
              <Wand2 size={20} className="text-ink-faint" />
            </div>
            <div>
              <div className="text-sm text-ink-secondary">暂无技能文件</div>
              <div className="text-xs text-ink-faint mt-1">在 .mycode/skills/ 目录下创建 .md 文件</div>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {skills.map((skill) => (
              <div key={skill.name} className="rounded-xl border border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover transition-all overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2.5 cursor-pointer" onClick={() => toggleSkill(skill.name)}>
                  {expandedSkill === skill.name ? (
                    <ChevronDown size={12} className="text-ink-muted flex-shrink-0" />
                  ) : (
                    <ChevronRight size={12} className="text-ink-muted flex-shrink-0" />
                  )}
                  <FileText size={12} className="text-accent flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-ink-strong truncate">{skill.name}</div>
                    {skill.description && (
                      <div className="text-xxs text-ink-muted truncate mt-0.5">{skill.description}</div>
                    )}
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(skill.name) }}
                    className="p-1 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-status-error-light text-ink-muted hover:text-status-error transition-all"
                    title="删除"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>

                {expandedSkill === skill.name && (
                  <div className="px-3 pb-3 border-t border-line-subtle">
                    {contentLoading ? (
                      <div className="flex items-center gap-2 py-3 text-xs text-ink-muted">
                        <RefreshCcw size={11} className="animate-spin" />
                        <span>加载中...</span>
                      </div>
                    ) : (
                      <pre className="text-xxs text-ink-secondary mt-2 whitespace-pre-wrap break-words max-h-48 overflow-y-auto font-mono leading-relaxed bg-surface-2 rounded-lg p-2.5">
                        {skillContent}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
