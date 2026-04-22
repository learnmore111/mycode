import { apiFetch } from './client'

export interface SkillInfo {
  name: string
  description: string
  path?: string | null
}

export interface SkillDetail {
  name: string
  path: string
  content: string
}

export async function listSkills(): Promise<SkillInfo[]> {
  return apiFetch<SkillInfo[]>('/skill')
}

export async function getSkill(name: string): Promise<SkillDetail> {
  return apiFetch<SkillDetail>(`/skill/${encodeURIComponent(name)}`)
}

export async function deleteSkill(name: string): Promise<void> {
  await apiFetch(`/skill/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export async function createSkill(name: string, content: string, scope: string = 'project'): Promise<void> {
  await apiFetch('/skill', {
    method: 'POST',
    body: JSON.stringify({ name, content, scope }),
  })
}

export async function uploadSkill(file: File, name?: string, scope: string = 'project'): Promise<void> {
  const formData = new FormData()
  formData.append('file', file)
  if (name) formData.append('name', name)
  formData.append('scope', scope)

  const res = await fetch('/skill/upload', {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
}
