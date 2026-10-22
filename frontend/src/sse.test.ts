import { describe, expect, it } from 'vitest'
import { parseBlock, SseParser } from './sse'

const enc = new TextEncoder()
const token = (text: string) => `event: token\ndata: ${JSON.stringify({ text })}\n\n`

describe('SseParser', () => {
  it('一個 chunk 裡有多個事件', () => {
    const p = new SseParser()
    const events = p.push(enc.encode(token('台積電') + token('營收')))
    expect(events).toEqual([
      { event: 'token', data: { text: '台積電' } },
      { event: 'token', data: { text: '營收' } },
    ])
  })

  it('事件被切在兩個 chunk 之間', () => {
    const p = new SseParser()
    const raw = token('合併營業收入')
    expect(p.push(enc.encode(raw.slice(0, 15)))).toEqual([])
    expect(p.push(enc.encode(raw.slice(15)))).toEqual([{ event: 'token', data: { text: '合併營業收入' } }])
  })

  it('中文字的 UTF-8 位元組被切開', () => {
    const p = new SseParser()
    const bytes = enc.encode(token('仟元'))
    const cut = bytes.indexOf(0xe4) + 1 // 切在「仟」的第一個位元組後面
    expect(p.push(bytes.slice(0, cut))).toEqual([])
    expect(p.push(bytes.slice(cut))).toEqual([{ event: 'token', data: { text: '仟元' } }])
  })

  it('解析 sources 與 error 事件', () => {
    const p = new SseParser()
    const sources = [{ index: 1, ticker: '2330', company: '台積電', year: 2025, page: 65, kind: 'table' }]
    const events = p.push(
      enc.encode(`event: sources\ndata: ${JSON.stringify(sources)}\n\nevent: error\ndata: {"message": "boom"}\n\n`),
    )
    expect(events).toEqual([
      { event: 'sources', data: sources },
      { event: 'error', data: { message: 'boom' } },
    ])
  })

  it('支援 CRLF 換行', () => {
    const p = new SseParser()
    expect(p.push(enc.encode('event: token\r\ndata: {"text": "a"}\r\n\r\n'))).toEqual([
      { event: 'token', data: { text: 'a' } },
    ])
  })

  it('flush 處理最後沒有空行結尾的事件', () => {
    const p = new SseParser()
    expect(p.push(enc.encode('event: token\ndata: {"text": "尾"}'))).toEqual([])
    expect(p.flush()).toEqual([{ event: 'token', data: { text: '尾' } }])
  })
})

describe('parseBlock', () => {
  it('不認得的事件與沒有 data 的區塊略過', () => {
    expect(parseBlock('event: ping\ndata: {}')).toEqual([])
    expect(parseBlock(': keep-alive')).toEqual([])
  })
})
