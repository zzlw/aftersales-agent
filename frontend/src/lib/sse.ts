// SSE over fetch（POST）解析器：EventSource 不支持 POST，这里手写流解析
export type SSEFrame = {
  event: string;
  data: Record<string, unknown>;
};

export async function* parseSSE(
  res: Response,
  signal?: AbortSignal
): AsyncGenerator<SSEFrame> {
  if (!res.body) throw new Error("empty body");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    if (signal?.aborted) break;
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // 统一将 \r\n 转为 \n，兼容 sse-starlette 输出的 CRLF
    buf = buf.replace(/\r\n/g, "\n");

    // SSE 以空行分隔事件块
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      try {
        yield { event, data: JSON.parse(data) };
      } catch {
        // 忽略无法解析的心跳/注释帧
      }
    }
  }
}
