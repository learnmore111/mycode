import { apiFetch } from './client'
import type { GitDiffDetail, GitStatus } from '../types'

function buildParams(directory?: string, extra?: Record<string, string>) {
  const params = new URLSearchParams()
  if (directory) params.set('directory', directory)
  for (const [key, value] of Object.entries(extra ?? {})) {
    params.set(key, value)
  }
  return params.toString()
}

export async function getGitStatus(directory?: string): Promise<GitStatus> {
  const query = buildParams(directory)
  return apiFetch<GitStatus>(`/git/status${query ? `?${query}` : ''}`)
}

export async function getGitDiff(path: string, directory?: string): Promise<GitDiffDetail> {
  const query = buildParams(directory, { path })
  return apiFetch<GitDiffDetail>(`/git/diff?${query}`)
}

export async function stageGitFile(path: string, directory?: string): Promise<void> {
  await apiFetch('/git/stage', {
    method: 'POST',
    body: JSON.stringify({ path, directory: directory || '.' }),
  })
}

export async function revertGitFile(path: string, directory?: string): Promise<void> {
  await apiFetch('/git/revert', {
    method: 'POST',
    body: JSON.stringify({ path, directory: directory || '.' }),
  })
}
