/** 卡片展開時顯示表格 chunk：content 是「表名 + markdown 表格」，只處理這個固定格式，不引入 markdown 套件。 */
export function ChunkContent({ content, isTable }: { content: string; isTable: boolean }) {
  if (!isTable) return <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{content}</p>

  const lines = content.split('\n')
  const rows = lines
    .filter((l) => l.startsWith('|') && !/^\|[-\s|]+\|$/.test(l))
    .map((l) => l.slice(1, -1).split(' | ').map((c) => c.trim()))
  const [header, ...body] = rows
  if (!header) return <p className="whitespace-pre-wrap text-sm">{content}</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} className="border border-slate-200 bg-slate-100 px-2 py-1 text-left font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className="even:bg-slate-50">
              {r.map((c, j) => (
                <td
                  key={j}
                  className={`border border-slate-200 px-2 py-1 whitespace-nowrap ${j > 0 ? 'text-right font-mono tabular-nums' : ''}`}
                >
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
