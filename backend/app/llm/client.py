"""模型适配层：统一 OpenAI 兼容协议，屏蔽 Provider 差异。

设计取舍（POC）：不引入 langchain-openai，直接用 openai SDK ——
- 流式 token 由 LangGraph custom stream writer 透传，Provider 无关；
- 结构化输出统一走 JSON mode + Pydantic 校验，避免各家 function-call 差异。
"""
import json
from typing import AsyncIterator, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

T = TypeVar("T", bound=BaseModel)

_llm = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
_embed = AsyncOpenAI(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key)


async def chat_stream(messages: list[dict], model: str | None = None,
                      temperature: float = 0.3) -> AsyncIterator[str]:
    """流式对话，逐 token 产出。"""
    stream = await _llm.chat.completions.create(
        model=model or settings.llm_model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def chat_completion(messages: list[dict], model: str | None = None,
                          temperature: float = 0.3) -> str:
    """非流式对话（用于 query 改写等内部调用）。"""
    resp = await _llm.chat.completions.create(
        model=model or settings.llm_model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def structured_output(messages: list[dict], schema: Type[T],
                            model: str | None = None) -> T:
    """结构化输出：JSON mode + Pydantic 校验，失败重试一次。

    用于意图路由 / 检索评估等小快模型调用（延迟控制点）。
    """
    sys_hint = (f"\n\n你必须只输出一个 JSON 对象（无 markdown 代码块），"
                f"符合以下 JSON Schema：\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}")
    msgs = [dict(m) for m in messages]
    msgs[0] = {"role": "system", "content": msgs[0]["content"] + sys_hint}

    last_err: Exception | None = None
    for _ in range(2):
        resp = await _llm.chat.completions.create(
            model=model or settings.llm_model_fast,
            messages=msgs,
            temperature=0,
            response_format={"type": "json_object"},
        )
        try:
            return schema.model_validate_json(resp.choices[0].message.content or "{}")
        except Exception as e:  # 校验失败带错误信息重试
            last_err = e
            msgs.append({"role": "assistant", "content": resp.choices[0].message.content or ""})
            msgs.append({"role": "user", "content": f"JSON 校验失败：{e}。请重新只输出合法 JSON。"})
    raise last_err  # type: ignore[misc]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化（bge-m3，中英双语）。"""
    resp = await _embed.embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in resp.data]
