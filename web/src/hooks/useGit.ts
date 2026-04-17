import { useCallback, useEffect, useState } from 'react'
import { getGitDiff, getGitStatus } from '../api/git'
import type { GitDiffDetail, GitStatus } from '../types'

export function useGit(directory?: string) {
  const [status, setStatus] = useState<GitStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [diff, setDiff] = useState<GitDiffDetail | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const next = await getGitStatus(directory)
      setStatus(next)

      if (selectedPath && !next.files.some((item) => item.path === selectedPath)) {
        setSelectedPath(null)
        setDiff(null)
      }
    } catch (err) {
      console.error('Failed to load git status', err)
      setError(err instanceof Error ? err.message : '加载 Git 状态失败')
    } finally {
      setLoading(false)
    }
  }, [directory, selectedPath])

  useEffect(() => {
    refresh().catch(() => undefined)
  }, [refresh])

  const openDiff = useCallback(
    async (path: string) => {
      setSelectedPath(path)
      setDiffLoading(true)
      setDiffError(null)
      try {
        const detail = await getGitDiff(path, directory)
        setDiff(detail)
      } catch (err) {
        console.error('Failed to load git diff', err)
        setDiff(null)
        setDiffError(err instanceof Error ? err.message : '加载 diff 失败')
      } finally {
        setDiffLoading(false)
      }
    },
    [directory],
  )

  const closeDiff = useCallback(() => {
    setSelectedPath(null)
    setDiff(null)
    setDiffError(null)
  }, [])

  return {
    status,
    loading,
    error,
    refresh,
    selectedPath,
    diff,
    diffLoading,
    diffError,
    openDiff,
    closeDiff,
  }
}
