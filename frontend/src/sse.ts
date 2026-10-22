import type { SseEvent } from './types'

/**
 * 把串流進來的位元組切成 SSE 事件。
 *
 * fetch 的 chunk 邊界跟事件邊界無關：一個 chunk 可能有半個事件，也可能有好幾個，
 * 中文字的 UTF-8 位元組也可能被切開。所以用 TextDecoder 的 stream 模式解碼，
 * 並把還沒遇到空行（事件結束）的部分留在 buffer。
 */
export class SseParser {
  private buffer = ''
  private decoder = new TextDecoder()

  push(chunk: Uint8Array): SseEvent[] {
    this.buffer += this.decoder.decode(chunk, { stream: true }).replace(/\r\n/g, '\n')
    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() ?? ''
    return blocks.flatMap((block) => parseBlock(block))
  }

  /** 串流結束時，處理最後一個沒有以空行結尾的事件。 */
  flush(): SseEvent[] {
    const rest = this.buffer + this.decoder.decode()
    this.buffer = ''
    return rest.trim() ? parseBlock(rest) : []
  }
}

export function parseBlock(block: string): SseEvent[] {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  if (!data.length) return []
  // 後端每個 data 都是 JSON；不認得的事件名稱略過，讓後端以後加事件時舊前端不會壞
  if (event !== 'token' && event !== 'sources' && event !== 'error') return []
  return [{ event, data: JSON.parse(data.join('\n')) } as SseEvent]
}
