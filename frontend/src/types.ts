// 對應後端 backend/app/schemas.py 的 Pydantic model

export interface Company {
  ticker: string
  company: string
  years: number[]
}

export interface Source {
  index: number
  ticker: string
  company: string
  year: number
  page: number
  kind: 'text' | 'table'
  title: string | null
  snippet: string
  content: string
}

export type SseEvent =
  | { event: 'token'; data: { text: string } }
  | { event: 'sources'; data: Source[] }
  | { event: 'error'; data: { message: string } }
