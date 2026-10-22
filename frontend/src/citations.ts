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
