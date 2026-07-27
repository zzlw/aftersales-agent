"""Agent 全局状态（对应方案 3.1，字段一次定义到位，后续阶段逐步启用）。

POC 取舍：messages 用普通 dict（OpenAI 格式）而非 LangChain Message 对象，
减少一层抽象、SSE 透传与 Prompt 拼装更直接。
"""
import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    # 对话历史（Reducer 追加；生成前做窗口裁剪 + 溢出摘要压缩）
    messages: Annotated[list[dict], operator.add]

    # 意图路由结果（D3 启用）
    intent: str            # usage | troubleshoot | policy | chitchat | out_of_scope | clarify | repeat
    language: str          # zh | en，跟随用户语言回复
    emotion: str           # neutral | frustrated | angry
    product_line: str      # 问题主体产品线（路由顺带输出，供 grade 校验）

    # RAG（D2 启用）
    retrieved_docs: list[dict]      # 检索结果（含 score / 来源元数据）
    retrieval_grade: str            # sufficient | partial | none

    # 防死循环 / 重复识别 / 工单（D3 启用）
    clarify_count: int
    answered_topics: Annotated[list[dict], operator.add]   # {topic, conclusion}
    ticket: dict

    # 本轮中间结果（每轮覆写，不依赖历史语义）
    _route: dict          # 路由结构化输出全量
    _query: str           # 本轮检索查询（指代消解后）
