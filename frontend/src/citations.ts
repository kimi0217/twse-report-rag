export type Segment = { type: 'text'; text: string } | { type: 'cite'; index: number; raw: string }

/**
 * 把回答拆成文字與 [n] 引用。只有 1～maxIndex 的編號做成連結：
 * 模型偶爾會寫出不存在的編號（例如 [9]），那種就當一般文字，免得點了沒反應。
 * 「[1][3]」「[1, 3]」都支援。
 */
export function splitCitations(answer: string, maxIndex: number): Segment[] {
  const segments: Segment[] = []
  const pattern = /\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g
  let last = 0
  for (const match of answer.matchAll(pattern)) {
    const nums = match[1].split(/\s*[,，、]\s*/).map(Number)
    if (!nums.every((n) => n >= 1 && n <= maxIndex)) continue
    pushText(segments, answer.slice(last, match.index))
    for (const n of nums) segments.push({ type: 'cite', index: n, raw: `[${n}]` })
    last = match.index + match[0].length
  }
  pushText(segments, answer.slice(last))
  return segments
}

function pushText(segments: Segment[], text: string) {
  if (!text) return
  const prev = segments[segments.length - 1]
  if (prev?.type === 'text') prev.text += text
  else segments.push({ type: 'text', text })
}

/** 回答裡實際引用到的編號（去重、依編號排序）。 */
export function citedIndices(answer: string, maxIndex: number): number[] {
  const set = new Set<number>()
  for (const seg of splitCitations(answer, maxIndex)) if (seg.type === 'cite') set.add(seg.index)
  return [...set].sort((a, b) => a - b)
}

/**
 * 決定哪些引用卡片預設顯示：回答裡有 [n] 就只顯示被引用的卡片，其餘收進「其他參考段落」；
 * 回答裡完全沒有 [n]（小模型偶爾不標）就顯示前 fallback 張，避免使用者看不到任何來源。
 */
export function partitionSources<T extends { index: number }>(
  answer: string,
  sources: T[],
  fallback = 3,
): { primary: T[]; others: T[] } {
  const cited = new Set(citedIndices(answer, sources.length))
  if (cited.size === 0) return { primary: sources.slice(0, fallback), others: sources.slice(fallback) }
  return {
    primary: sources.filter((s) => cited.has(s.index)),
    others: sources.filter((s) => !cited.has(s.index)),
  }
}
