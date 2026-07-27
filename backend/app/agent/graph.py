"""LangGraph 状态图（完整版，对应方案 3.2）。

拓扑：
  START → intent_router ─┬─ repeat   → END
                         ├─ redirect → END
                         ├─ clarify  → END（等待用户补充，下轮重新路由）
                         └─ retrieve → grade ─┬─ generate → END
                                              └─ fallback → END（内部含改写重试一次）
"""
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    clarify_node,
    fallback_node,
    generate_node,
    grade_decision,
    grade_node,
    intent_router,
    redirect_node,
    repeat_node,
    retrieve_node,
    route_decision,
)
from app.agent.state import AgentState


def build_graph(checkpointer):
    g = StateGraph(AgentState)

    g.add_node("intent_router", intent_router)
    g.add_node("clarify", clarify_node)
    g.add_node("redirect", redirect_node)
    g.add_node("repeat", repeat_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("generate", generate_node)
    g.add_node("fallback", fallback_node)

    g.add_edge(START, "intent_router")
    g.add_conditional_edges("intent_router", route_decision, {
        "repeat": "repeat",
        "redirect": "redirect",
        "clarify": "clarify",
        "retrieve": "retrieve",
    })
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", grade_decision, {
        "generate": "generate",
        "fallback": "fallback",
    })
    for terminal in ("repeat", "redirect", "clarify", "generate", "fallback"):
        g.add_edge(terminal, END)

    return g.compile(checkpointer=checkpointer)
