import { describe, expect, it } from 'vitest'
import { splitCitations } from './citations'

describe('splitCitations', () => {
  it('把 [n] 拆成引用片段', () => {
    expect(splitCitations('營收為 3,809 億 [1]。', 8)).toEqual([
      { type: 'text', text: '營收為 3,809 億 ' },
      { type: 'cite', index: 1, raw: '[1]' },
      { type: 'text', text: '。' },
    ])
  })

  it('連續與逗號分隔的引用', () => {
    const cites = (s: string) => splitCitations(s, 8).filter((x) => x.type === 'cite').map((x) => x.type === 'cite' && x.index)
    expect(cites('見 [1][3]')).toEqual([1, 3])
    expect(cites('見 [2, 4]')).toEqual([2, 4])
    expect(cites('見 [2，5]')).toEqual([2, 5])
  })

  it('超出範圍的編號當一般文字', () => {
    expect(splitCitations('見 [9] 與 [0]', 8)).toEqual([{ type: 'text', text: '見 [9] 與 [0]' }])
  })

  it('沒有引用時整段是文字', () => {
    expect(splitCitations('年報中查無相關資料', 0)).toEqual([{ type: 'text', text: '年報中查無相關資料' }])
  })

  it('非數字的方括號不受影響', () => {
    expect(splitCitations('**19.45 元** [註2]', 8)).toEqual([{ type: 'text', text: '**19.45 元** [註2]' }])
  })
})
