import { SseParser } from './sse'
import type { Company, SseEvent } from './types'

// 一律走相對路徑 /api：開發時由 Vite proxy 轉給後端，不寫死網址
const API = '/api'

export class BackendUnavailable extends Error {}

export async function fetchCompanies(): Promise<Company[]> {
  const resp = await fetch(`${API}/companies`).catch(() => {
    throw new BackendUnavailable()
  })
  // Vite proxy 連不上後端時回 502/504，也當成後端沒啟動
  if (!resp.ok) throw new BackendUnavailable(`HTTP ${resp.status}`)
  return resp.json()
}

export interface AskParams {
  question: string
  tickers?: string[]
  signal?: AbortSignal
}

export async function askStream({ question, tickers, signal }: AskParams, onEvent: (e: SseEvent) => void) {
  const resp = await fetch(`${API}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(tickers ? { question, tickers } : { question }),
    signal,
  }).catch((err) => {
    if (err?.name === 'AbortError') throw err
    throw new BackendUnavailable()
  })
  if (!resp.ok || !resp.body) {
    if (resp.status >= 500) throw new BackendUnavailable(`HTTP ${resp.status}`)
    throw new Error(`HTTP ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const parser = new SseParser()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parser.push(value).forEach(onEvent)
  }
  parser.flush().forEach(onEvent)
}
