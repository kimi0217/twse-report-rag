import type { Source } from './types'

export interface ChatMessage {
  id: string
  question: string
  answer: string
  sources: Source[]
  status: 'streaming' | 'done' | 'error'
  error?: { title: string; detail?: string }
}

export const cardId = (messageId: string, index: number) => `src-${messageId}-${index}`

export const EXAMPLES: { question: string; badge?: string }[] = [
  { question: '台積電 2025 年合併營業收入是多少？' },
  { question: '廣達 2025 年每股盈餘是多少？' },
  { question: '鴻海的主要產品有哪些？' },
  { question: '長榮航空 2025 年營收是多少？', badge: '拒答示範' },
]

export const BACKEND_DOWN = {
  title: '無法連線到後端服務',
  detail: '請確認已在專案根目錄執行：uvicorn backend.app.main:app --port 8000',
}
