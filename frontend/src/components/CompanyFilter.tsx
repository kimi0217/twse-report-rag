import type { Company } from '../types'

interface Props {
  companies: Company[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
  disabled: boolean
}

export function CompanyFilter({ companies, selected, onChange, disabled }: Props) {
  const allSelected = companies.length > 0 && selected.size === companies.length

  const toggle = (ticker: string) => {
    const next = new Set(selected)
    if (next.has(ticker)) next.delete(ticker)
    else next.add(ticker)
    onChange(next)
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold tracking-wider text-slate-500">公司篩選</h2>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChange(allSelected ? new Set() : new Set(companies.map((c) => c.ticker)))}
          className="text-xs text-blue-600 hover:underline disabled:text-slate-400"
        >
          {allSelected ? '全不選' : '全選'}
        </button>
      </div>
      <ul className="space-y-1">
        {companies.map((c) => (
          <li key={c.ticker}>
            <label className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 hover:bg-slate-100">
              <input
                type="checkbox"
                checked={selected.has(c.ticker)}
                onChange={() => toggle(c.ticker)}
                disabled={disabled}
                className="size-4 accent-blue-600"
              />
              <span className="flex-1 text-sm">{c.company}</span>
              <span className="font-mono text-xs text-slate-400">{c.ticker}</span>
            </label>
          </li>
        ))}
      </ul>
      {companies.length > 0 && selected.size === 0 && (
        <p className="mt-2 text-xs text-amber-600">請至少選一家公司</p>
      )}
      <p className="mt-4 text-xs leading-relaxed text-slate-400">
        資料來源：各公司 {companies[0]?.years.at(-1) ?? ''} 年度股東會年報
      </p>
    </section>
  )
}
