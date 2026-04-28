import type { Message, Part, SessionSummary, SessionSummaryDiff, SessionCodeChange } from '../types'

const MUTATING_TOOLS = new Set(['edit', 'write'])

function tryMatch(patterns: RegExp[], text: string): string | null {
  for (const pattern of patterns) {
    const match = text.match(pattern)
    const value = match?.[1]?.trim()
    if (value) return value
  }
  return null
}

export function buildResumePrompt(lastUserText: string, partialText?: string): string {
  const sections = [
    '继续处理我上一个被暂停的请求。',
    `上一个请求：${lastUserText}`,
  ]

  if (partialText?.trim()) {
    sections.push(`暂停前你已经输出了部分内容：${partialText.trim().slice(0, 400)}`)
  }

  sections.push('请先检查当前会话历史和工作区里已经完成的代码修改，再从中断处继续，不要重复已经做完的步骤。')

  return sections.join('\n\n')
}

export function getSessionSearchText(summary?: SessionSummary | null): string {
  if (!summary) return ''
  const parts: string[] = []
  if (summary.files) parts.push(`${summary.files} 文件`)
  if (summary.additions) parts.push(`+${summary.additions}`)
  if (summary.deletions) parts.push(`-${summary.deletions}`)
  if (summary.diffs?.length) {
    parts.push(...summary.diffs.map((diff) => (typeof diff === 'string' ? diff : diff.file ?? '')).filter(Boolean))
  }
  return parts.join(' ')
}

export function getSessionSummaryBadges(summary?: SessionSummary | null): string[] {
  if (!summary) return []
  const badges: string[] = []
  if (summary.files) badges.push(`${summary.files} 个文件`)
  if (summary.additions) badges.push(`+${summary.additions}`)
  if (summary.deletions) badges.push(`-${summary.deletions}`)
  return badges
}

function normalizeDiffEntry(diff: SessionSummaryDiff): string | null {
  if (typeof diff === 'string') return diff
  return diff.file || diff.path || diff.label || null
}

export function extractSummaryFiles(summary?: SessionSummary | null): string[] {
  if (!summary?.diffs?.length) return []
  return summary.diffs
    .map(normalizeDiffEntry)
    .filter((value): value is string => Boolean(value))
}

export function extractToolTargetFile(part: Part): string | null {
  if (part.type !== 'tool' || !part.tool) return null

  const output = String(part.state?.output ?? part.content ?? '')
  return tryMatch(
    [
      /^Edited\s+(.+?)(?:\s+\(|$)/m,
      /^Overwrote\s+(.+?)(?:\s+\(|$)/m,
      /^Created\s+(.+?)(?:\s+\(|$)/m,
      /^Appended to\s+(.+?)(?:\s+\(|$)/m,
      /^Inserted\s+\d+\s+line\(s\)\s+after\s+line\s+\d+\s+in\s+(.+?)(?:\s+\(|$)/m,
    ],
    output,
  )
}

export function extractSessionCodeChanges(messages: Message[], summary?: SessionSummary | null, limit = 6): SessionCodeChange[] {
  const seen = new Set<string>()
  const changes: SessionCodeChange[] = []

  for (const message of [...messages].reverse()) {
    for (const part of [...message.parts].reverse()) {
      if (part.type !== 'tool') continue
      if (!part.tool || !MUTATING_TOOLS.has(part.tool)) continue

      const filePath = extractToolTargetFile(part)
      const key = `${part.tool}:${filePath ?? part.id}`
      if (seen.has(key)) continue
      seen.add(key)

      changes.push({
        id: key,
        tool: part.tool,
        filePath,
        time: part.time.completed ?? part.time.created,
        preview: String(part.state?.output ?? part.content ?? '').split('\n').slice(0, 2).join(' '),
      })

      if (changes.length >= limit) return changes
    }
  }

  if (changes.length === 0) {
    for (const file of extractSummaryFiles(summary).slice(0, limit)) {
      changes.push({
        id: `summary:${file}`,
        tool: 'summary',
        filePath: file,
        time: 0,
        preview: '来自会话改动摘要',
      })
    }
  }

  return changes
}
