import type { CompactionEvent } from '../types'

export async function getCompactionEvents(sessionId: string): Promise<CompactionEvent[]> {
  const response = await fetch(`/session/${sessionId}/compaction-events`)
  if (!response.ok) {
    throw new Error(`Failed to fetch compaction events: ${response.statusText}`)
  }
  return response.json()
}
