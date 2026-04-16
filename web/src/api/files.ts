import { apiFetch } from './client'

export interface FileEntry {
  name: string
  type: 'file' | 'directory'
  path: string
  size?: number
}

export interface FileContent {
  type: 'text' | 'binary'
  content: string
  encoding?: string
  mime_type?: string
}

export async function listDir(path?: string): Promise<FileEntry[]> {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  return apiFetch<FileEntry[]>(`/file/list?${params}`)
}

export async function readFile(path: string): Promise<FileContent> {
  return apiFetch<FileContent>(`/file?path=${encodeURIComponent(path)}`)
}

export async function searchFiles(query: string, limit = 20): Promise<string[]> {
  return apiFetch<string[]>(`/file/search?query=${encodeURIComponent(query)}&limit=${limit}`)
}
