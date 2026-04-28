import { useCallback, useRef, useState } from 'react'
import {
  RefreshCcw,
  Trash2,
  Wand2,
  ChevronDown,
  ChevronRight,
  FileText,
  Upload,
  X,
  FolderOpen,
  Check,
  AlertCircle,
} from 'lucide-react'
import { getSkill, deleteSkill, uploadSkill } from '../api/skills'
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
  const [showUpload, setShowUpload] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadScope, setUploadScope] = useState<'project' | 'global'>('project')
  const [dragOver, setDragOver] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  const addFiles = useCallback((files: FileList | File[]) => {
    const mdFiles = Array.from(files).filter(
      (f) => f.name.endsWith('.md') || f.name.endsWith('.txt')
    )
    if (mdFiles.length === 0) {
      setUploadStatus({ type: 'error', message: '请选择 .md 或 .txt 文件' })
      setTimeout(() => setUploadStatus(null), 3000)
      return
    }
    setSelectedFiles((prev) => {
      const names = new Set(prev.map((f) => f.name))
      return [...prev, ...mdFiles.filter((f) => !names.has(f.name))]
    })
    setUploadStatus(null)
  }, [])

  const removeFile = (name: string) => {
    setSelectedFiles((prev) => prev.filter((f) => f.name !== name))
  }

  const handleUpload = async () => {
    if (selectedFiles.length === 0) return
    setUploading(true)
    setUploadStatus(null)
    let successCount = 0
    let failCount = 0

    for (const file of selectedFiles) {
      try {
        await uploadSkill(file, undefined, uploadScope)
        successCount++
      } catch (err) {
        console.error('Upload skill failed', err)
        failCount++
      }
    }

    if (failCount === 0) {
      setUploadStatus({ type: 'success', message: `成功上传 ${successCount} 个技能文件` })
      setSelectedFiles([])
      setShowUpload(false)
      onRefresh()
    } else {
      setUploadStatus({
        type: 'error',
        message: `上传完成：${successCount} 个成功，${failCount} 个失败`,
      })
      onRefresh()
    }
    setUploading(false)
    setTimeout(() => setUploadStatus(null), 4000)
  }

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setDragOver(false)
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files)
        if (!showUpload) setShowUpload(true)
      }
    },
    [addFiles, showUpload]
  )

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4 py-10">
        <RefreshCcw size={16} className="animate-spin text-accent" />
        <span className="text-xs text-ink-muted">加载技能列表...</span>
      </div>
    )
  }

  return (
    <div
      className="flex-1 flex flex-col min-h-0"
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {/* Header card */}
      <div className="px-3 pb-2">
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

      {/* Upload button */}
      <div className="px-3 pb-2">
        <button
          onClick={() => {
            setShowUpload(!showUpload)
            if (showUpload) {
              setSelectedFiles([])
              setUploadStatus(null)
            }
          }}
          className={`w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-xs font-medium transition-all ${
            showUpload
              ? 'bg-surface-2 text-ink-secondary border border-line'
              : 'bg-accent text-white hover:bg-accent-hover shadow-xs'
          }`}
        >
          {showUpload ? <X size={12} /> : <Upload size={12} />}
          <span>{showUpload ? '取消上传' : '上传技能'}</span>
        </button>
      </div>

      {/* Upload area */}
      {showUpload && (
        <div className="px-3 pb-3 animate-slide-up">
          <div className="rounded-xl border border-line bg-surface-1 overflow-hidden">
            {/* Drop zone */}
            <div
              onClick={() => fileInputRef.current?.click()}
              className={`relative flex flex-col items-center justify-center gap-2 px-4 py-5 cursor-pointer transition-all border-b border-line-subtle ${
                dragOver
                  ? 'bg-accent/5 border-accent/30'
                  : 'bg-surface-0 hover:bg-surface-hover'
              }`}
            >
              <div
                className={`w-10 h-10 rounded-xl flex items-center justify-center transition-colors ${
                  dragOver ? 'bg-accent/10' : 'bg-surface-2'
                }`}
              >
                <FolderOpen
                  size={18}
                  className={dragOver ? 'text-accent' : 'text-ink-muted'}
                />
              </div>
              <div className="text-center">
                <div className="text-xs font-medium text-ink-secondary">
                  {dragOver ? '释放以添加文件' : '点击选择或拖拽文件到此处'}
                </div>
                <div className="text-[10px] text-ink-faint mt-0.5">
                  支持 .md / .txt 格式，可多选
                </div>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".md,.txt"
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files)
                  e.target.value = ''
                }}
              />
            </div>

            {/* Selected files list */}
            {selectedFiles.length > 0 && (
              <div className="px-3 py-2 space-y-1 max-h-32 overflow-y-auto">
                {selectedFiles.map((file) => (
                  <div
                    key={file.name}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-surface-2 group"
                  >
                    <FileText size={11} className="text-accent flex-shrink-0" />
                    <span className="text-[11px] text-ink-secondary truncate flex-1 font-mono">
                      {file.name}
                    </span>
                    <span className="text-[10px] text-ink-faint">
                      {(file.size / 1024).toFixed(1)}KB
                    </span>
                    <button
                      onClick={() => removeFile(file.name)}
                      className="p-0.5 rounded hover:bg-status-error-light text-ink-faint hover:text-status-error transition-colors opacity-0 group-hover:opacity-100"
                    >
                      <X size={10} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Scope + upload action */}
            <div className="px-3 py-2.5 flex items-center justify-between border-t border-line-subtle bg-surface-0">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] text-ink-faint mr-1">作用域</span>
                <button
                  onClick={() => setUploadScope('project')}
                  className={`px-2 py-1 rounded-md text-[10px] font-medium transition-colors ${
                    uploadScope === 'project'
                      ? 'bg-accent text-white shadow-xs'
                      : 'bg-surface-2 text-ink-muted hover:text-ink-secondary'
                  }`}
                >
                  项目
                </button>
                <button
                  onClick={() => setUploadScope('global')}
                  className={`px-2 py-1 rounded-md text-[10px] font-medium transition-colors ${
                    uploadScope === 'global'
                      ? 'bg-accent text-white shadow-xs'
                      : 'bg-surface-2 text-ink-muted hover:text-ink-secondary'
                  }`}
                >
                  全局
                </button>
              </div>
              <button
                onClick={handleUpload}
                disabled={uploading || selectedFiles.length === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-white text-[11px] font-medium hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-xs"
              >
                {uploading ? (
                  <RefreshCcw size={10} className="animate-spin" />
                ) : (
                  <Upload size={10} />
                )}
                <span>上传 {selectedFiles.length > 0 ? `(${selectedFiles.length})` : ''}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Upload status toast */}
      {uploadStatus && (
        <div className="px-3 pb-2 animate-slide-up">
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] font-medium ${
              uploadStatus.type === 'success'
                ? 'bg-status-success/10 text-status-success border border-status-success/20'
                : 'bg-status-error/10 text-status-error border border-status-error/20'
            }`}
          >
            {uploadStatus.type === 'success' ? <Check size={12} /> : <AlertCircle size={12} />}
            <span>{uploadStatus.message}</span>
          </div>
        </div>
      )}

      {/* Skills list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {skills.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
            <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
              <Wand2 size={20} className="text-ink-faint" />
            </div>
            <div>
              <div className="text-sm text-ink-secondary">暂无技能文件</div>
              <div className="text-xs text-ink-faint mt-1">
                上传 .md 文件到 .mycode/skills/ 目录
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {skills.map((skill) => (
              <div
                key={skill.name}
                className="group rounded-xl border border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover transition-all overflow-hidden"
              >
                <div
                  className="flex items-center gap-2 px-3 py-2.5 cursor-pointer"
                  onClick={() => toggleSkill(skill.name)}
                >
                  {expandedSkill === skill.name ? (
                    <ChevronDown size={12} className="text-ink-muted flex-shrink-0" />
                  ) : (
                    <ChevronRight size={12} className="text-ink-muted flex-shrink-0" />
                  )}
                  <FileText size={12} className="text-accent flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-ink-strong truncate">
                      {skill.name}
                    </div>
                    {skill.description && (
                      <div className="text-xxs text-ink-muted truncate mt-0.5">
                        {skill.description}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDelete(skill.name)
                    }}
                    className="p-1.5 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-status-error-light text-ink-muted hover:text-status-error transition-all"
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

      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-accent/5 border-2 border-dashed border-accent/30 rounded-xl pointer-events-none">
          <div className="flex flex-col items-center gap-2">
            <Upload size={24} className="text-accent" />
            <span className="text-sm font-medium text-accent">释放以上传技能文件</span>
          </div>
        </div>
      )}
    </div>
  )
}
