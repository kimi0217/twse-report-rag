import { useCallback, useEffect, useRef, useState } from 'react'
import { askStream, BackendUnavailable, fetchCompanies } from './api'
import { BACKEND_DOWN, cardId, EXAMPLES, type ChatMessage } from './chat'
import { CompanyFilter } from './components/CompanyFilter'
import { Message } from './components/Message'
import type { Company } from './types'

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loadError, setLoadError] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [highlight, setHighlight] = useState<{ id: string; index: number } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const highlightTimer = useRef<number | undefined>(undefined)

  const lastStatus = messages.at(-1)?.status
  const busy = lastStatus === 'streaming'
  const noneSelected = companies.length > 0 && selected.size === 0

  const loadCompanies = useCallback(() => {
    setLoadError(false)
    fetchCompanies()
      .then((cs) => {
        setCompanies(cs)
        setSelected(new Set(cs.map((c) => c.ticker))) // 預設全選
      })
      .catch(() => setLoadError(true))
  }, [])

  useEffect(loadCompanies, [loadCompanies])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, lastStatus])

  const update = (id: string, fn: (m: ChatMessage) => ChatMessage) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)))

  const ask = async (question: string) => {
    question = question.trim()
    if (!question || busy || noneSelected) return
    const id = crypto.randomUUID()
    setMessages((ms) => [...ms, { id, question, answer: '', sources: [], status: 'streaming' }])
    setInput('')

    // 全選時不送 tickers：後端會依問題裡的公司名稱自動篩選；送出全部代號反而會關掉這個機制
    const tickers = selected.size === companies.length ? undefined : [...selected]
    try {
      await askStream({ question, tickers }, (e) => {
        if (e.event === 'token') update(id, (m) => ({ ...m, answer: m.answer + e.data.text }))
        else if (e.event === 'sources') update(id, (m) => ({ ...m, sources: e.data }))
        else update(id, (m) => ({ ...m, status: 'error', error: { title: '回答時發生錯誤', detail: e.data.message } }))
      })
      update(id, (m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m))
    } catch (err) {
      const error = err instanceof BackendUnavailable ? BACKEND_DOWN : { title: '回答時發生錯誤', detail: String(err) }
      update(id, (m) => ({ ...m, status: 'error', error }))
    }
  }

  const cite = (messageId: string, index: number) => {
    document.getElementById(cardId(messageId, index))?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setHighlight({ id: messageId, index })
    window.clearTimeout(highlightTimer.current)
    highlightTimer.current = window.setTimeout(() => setHighlight(null), 2000)
  }

  return (
    <div className="flex h-screen">
      <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white px-5 py-6">
        <div className="mb-8">
          <h1 className="text-lg font-semibold">年報問答助理</h1>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">從台灣上市公司年報檢索段落與財務表格，回答並附上引用來源。</p>
        </div>
        {loadError ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">
            <p className="font-medium">{BACKEND_DOWN.title}</p>
            <p className="mt-1 leading-relaxed">{BACKEND_DOWN.detail}</p>
            <button type="button" onClick={loadCompanies} className="mt-2 font-medium text-red-700 underline">
              重試
            </button>
          </div>
        ) : (
          <CompanyFilter companies={companies} selected={selected} onChange={setSelected} disabled={busy} />
        )}
        <p className="mt-auto text-xs text-slate-400">本機執行：bge-m3 + qwen3.5:2b（Ollama）</p>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-8 px-6 py-8">
            {messages.length === 0 && (
              <div className="pt-16 text-center">
                <h2 className="text-2xl font-semibold">想從年報裡查什麼？</h2>
                <p className="mt-2 text-sm text-slate-500">回答只根據年報內容，找不到就會說查無資料。</p>
                <div className="mx-auto mt-8 grid max-w-xl gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex.question}
                      type="button"
                      onClick={() => ask(ex.question)}
                      disabled={busy || noneSelected || loadError}
                      className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-left text-sm hover:border-blue-400 hover:bg-blue-50/40 disabled:opacity-50"
                    >
                      {ex.badge && (
                        <span className="mb-1 inline-block rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-amber-200">
                          {ex.badge}
                        </span>
                      )}
                      <span className="block">{ex.question}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <Message
                key={m.id}
                message={m}
                highlighted={highlight?.id === m.id ? highlight.index : null}
                onCite={(i) => cite(m.id, i)}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-slate-200 bg-white">
          <form
            className="mx-auto max-w-3xl px-6 py-4"
            onSubmit={(e) => {
              e.preventDefault()
              ask(input)
            }}
          >
            <p className="mb-2 text-xs text-slate-400">首次提問需要載入模型，約 15 秒</p>
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={busy}
                placeholder={busy ? '回答中…' : '輸入問題，例如：聯發科的研發費用是多少？'}
                maxLength={500}
                className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
              />
              <button
                type="submit"
                disabled={busy || !input.trim() || noneSelected}
                className="rounded-lg bg-blue-600 px-5 text-sm font-medium text-white hover:bg-blue-700 disabled:bg-slate-300"
              >
                {busy ? '回答中' : '送出'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  )
}
