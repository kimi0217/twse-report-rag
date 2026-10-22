import { Fragment } from 'react'
import { splitCitations } from '../citations'
import { cardId, type ChatMessage } from '../chat'
import { SourceCard } from './SourceCard'

interface Props {
  message: ChatMessage
  highlighted: number | null
  onCite: (index: number) => void
}

/** 模型偶爾輸出 **粗體**；只處理這一種，避免引入 markdown 套件 */
function Inline({ text }: { text: string }) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>,
  )
}

export function Message({ message: m, highlighted, onCite }: Props) {
  const segments = splitCitations(m.answer, m.sources.length)
  const streaming = m.status === 'streaming'

  return (
    <article className="space-y-3">
      <div className="flex justify-end">
        <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2 text-sm text-white">{m.question}</p>
      </div>

      <div className="rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-5 py-4">
        {m.answer ? (
          <p className="text-[15px] leading-7 whitespace-pre-wrap">
            {segments.map((seg, i) =>
              seg.type === 'text' ? (
                <Inline key={i} text={seg.text} />
              ) : (
                <button
                  key={i}
                  type="button"
                  onClick={() => onCite(seg.index)}
                  className="mx-0.5 rounded bg-blue-50 px-1 align-[1px] font-mono text-xs font-semibold text-blue-700 hover:bg-blue-100"
                  title={`查看來源 ${seg.index}`}
                >
                  {seg.index}
                </button>
              ),
            )}
            {streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-middle" />}
          </p>
        ) : streaming ? (
          <p className="flex items-center gap-2 text-sm text-slate-500">
            <span className="flex gap-1">
              {[0, 150, 300].map((d) => (
                <span key={d} className="size-1.5 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: `${d}ms` }} />
              ))}
            </span>
            檢索年報並產生回答中…
          </p>
        ) : null}

        {m.error && (
          <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <p className="font-medium">{m.error.title}</p>
            {m.error.detail && <p className="mt-0.5 font-mono text-xs text-red-500">{m.error.detail}</p>}
          </div>
        )}
      </div>

      {m.sources.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold tracking-wider text-slate-500">引用來源（{m.sources.length}）</h3>
          <ol className="grid gap-2">
            {m.sources.map((s) => (
              <SourceCard key={s.index} source={s} id={cardId(m.id, s.index)} highlighted={highlighted === s.index} />
            ))}
          </ol>
        </section>
      )}
    </article>
  )
}
