import { useState } from 'react'
import type { Source } from '../types'
import { ChunkContent } from './MarkdownTable'

interface Props {
  source: Source
  id: string
  highlighted: boolean
}

export function SourceCard({ source: s, id, highlighted }: Props) {
  const [open, setOpen] = useState(false)
  const isTable = s.kind === 'table'

  return (
    <li
      id={id}
      className={`scroll-mt-24 rounded-lg border bg-white transition-shadow duration-500 ${
        highlighted ? 'border-blue-500 shadow-[0_0_0_3px_rgba(59,130,246,0.25)]' : 'border-slate-200'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 p-3 text-left"
      >
        <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-slate-100 font-mono text-xs font-semibold text-slate-600">
          {s.index}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="font-medium">{s.company}</span>
            <span className="text-slate-500">
              {s.year} 年報・第 {s.page} 頁
            </span>
            {isTable && (
              <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200">
                表格
              </span>
            )}
          </span>
          {s.title && <span className="mt-0.5 block truncate text-xs text-slate-500">{s.title}</span>}
          {/* 表格的摘要是 markdown 原始碼，收合時不顯示；副標題已經有表名，展開才顯示整張表 */}
          {!open && !isTable && (
            <span className="mt-1.5 line-clamp-2 block text-xs leading-relaxed text-slate-600">{s.snippet}</span>
          )}
        </span>
        <span className={`mt-1 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 pt-2 pb-3">
          <ChunkContent content={s.content} isTable={isTable} />
        </div>
      )}
    </li>
  )
}
