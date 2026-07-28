"""状态图节点实现（核心链路，对应方案 3.2/3.3）。

设计要点落地：
- intent_router 一次结构化输出承担 6 项判断（延迟控制①）
- grade 两级判据：产品线元数据校验先于分数短路（防"词面相近误判"）
- fallback 节点内部完成"改写重试一次"，避免图上成环
- answered_topics 支撑重复提问识别
"""
from typing import Literal

from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agent import prompts
from app.agent.state import AgentState
from app.llm.client import chat_completion, chat_stream, embed_texts, structured_output
from app.rag.store import hybrid_search

MAX_HISTORY = 12          # 上下文窗口裁剪：System + 最近 N 条
TOP_K = 5
SUFFICIENT_SCORE = 0.031  # RRF 双榜第一 ≈ 2/61，达到即规则短路 sufficient（延迟控制②）

# 由 lifespan 注入的 PG 连接池（节点内做检索）
_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


class RouteResult(BaseModel):
    intent: Literal["usage", "troubleshoot", "policy", "chitchat", "out_of_scope"]
    needs_clarification: bool = False
    clarify_question: str = ""
    is_repeat: bool = False
    repeat_topic: str = ""
    language: Literal["zh", "en"] = "zh"
    emotion: Literal["neutral", "frustrated", "angry"] = "neutral"
    product_line: Literal["notebook", "desktop", "server", "printer", "phone", "unknown"] = "unknown"
    search_query: str = ""


def _recent_messages(state: AgentState) -> list[dict]:
    # 只保留 role/content：消息上挂载的 citations/suggests 元数据不能透传给 LLM API
    return [{"role": m["role"], "content": m["content"]}
            for m in state["messages"][-MAX_HISTORY:]]


def _lang_name(lang: str) -> str:
    return "中文" if lang == "zh" else "English"


# ────────────────────── 节点 ──────────────────────

async def intent_router(state: AgentState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "stage": "routing"})

    topics = state.get("answered_topics", [])[-8:]
    topics_text = "\n".join(f"- {t['topic']}：{t['conclusion']}" for t in topics) or "（无）"
    history = _recent_messages(state)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)

    route = await structured_output(
        [{"role": "system", "content": prompts.ROUTER_SYSTEM},
         {"role": "user", "content": f"已答话题清单：\n{topics_text}\n\n最近对话：\n{convo}"}],
        RouteResult,
    )
    thinking = (f"意图={route.intent} 语言={route.language} 情绪={route.emotion} "
                f"产品线={route.product_line} 澄清={route.needs_clarification} 重复={route.is_repeat}")
    writer({"type": "thinking", "text": thinking})
    return {
        "intent": route.intent, "language": route.language, "emotion": route.emotion,
        "product_line": route.product_line,
        # 借用 state 暂存本轮路由中间结果（不进 checkpoint 长期语义）
        "_route": route.model_dump(),
        # 本轮执行过程从这里重置，后续节点逐步追加，终态节点随消息持久化
        "_timeline": [{"kind": "thinking", "text": thinking}],
    }


def route_decision(state: AgentState) -> str:
    r = state["_route"]
    if r["is_repeat"]:
        return "repeat"
    if r["intent"] in ("chitchat", "out_of_scope"):
        return "redirect"
    if r["needs_clarification"] and state.get("clarify_count", 0) < 2:
        return "clarify"
    return "retrieve"     # clarify 超限也强制进入检索（防死循环）


async def clarify_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    question = state["_route"]["clarify_question"] or (
        "为了更准确地帮您排查，请告诉我产品型号和具体故障现象？"
        if state.get("language") == "zh"
        else "To help you better, could you share the product model and the exact symptom?")
    writer({"type": "delta", "text": question})
    return {"messages": [{"role": "assistant", "content": question,
                          "timeline": state.get("_timeline", [])}],
            "clarify_count": state.get("clarify_count", 0) + 1}


async def redirect_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    full = ""
    async for tok in chat_stream(
            [{"role": "system", "content": prompts.REDIRECT_SYSTEM},
             *_recent_messages(state)], temperature=0.5):
        full += tok
        writer({"type": "delta", "text": tok})
    writer({"type": "suggest", "items": _quick_questions(state.get("language", "zh"))})
    return {"messages": [{"role": "assistant", "content": full,
                          "timeline": state.get("_timeline", []),
                          "suggests": _quick_questions(state.get("language", "zh"))}],
            "clarify_count": 0}


async def repeat_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    topic = state["_route"]["repeat_topic"]
    matched = [t for t in state.get("answered_topics", []) if topic and topic in t["topic"]]
    conclusion = matched[-1]["conclusion"] if matched else \
        (state.get("answered_topics") or [{"conclusion": ""}])[-1]["conclusion"]
    full = ""
    async for tok in chat_stream(
            [{"role": "system", "content": prompts.REPEAT_SYSTEM},
             {"role": "user",
              "content": f"已答结论：{conclusion}\n\n用户最新消息：{state['messages'][-1]['content']}"}]):
        full += tok
        writer({"type": "delta", "text": tok})
    suggest = ["转人工客服" if state.get("language") == "zh" else "Talk to a human agent"]
    writer({"type": "suggest", "items": suggest, "action": "ticket"})
    return {"messages": [{"role": "assistant", "content": full,
                          "timeline": state.get("_timeline", []),
                          "suggests": suggest, "suggest_action": "ticket"}],
            "clarify_count": 0}


async def retrieve_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "stage": "retrieving"})
    query = state["_route"]["search_query"] or state["messages"][-1]["content"]
    docs = await _do_search(query)
    hits = [{"title": d["doc_title"], "section": d["section"],
             "score": round(float(d["score"]), 4)} for d in docs]
    writer({"type": "tool", "name": "hybrid_search", "query": query, "hits": hits})
    # 持久化文本与前端实时帧的拼接格式保持一致，刷新前后展示无差异
    hits_text = "；".join(f"{h['title']}·{h['section']}({h['score']})" for h in hits) or "无命中"
    return {"retrieved_docs": docs, "_query": query,
            "_timeline": state.get("_timeline", []) + [
                {"kind": "tool", "text": f'hybrid_search("{query}") → {hits_text}'}]}


async def grade_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "stage": "grading"})
    grade = await _grade(state["retrieved_docs"], state.get("product_line", "unknown"),
                         state["_query"])
    writer({"type": "thinking", "text": f"检索评估 = {grade}"})
    return {"retrieval_grade": grade,
            "_timeline": state.get("_timeline", []) + [
                {"kind": "thinking", "text": f"检索评估 = {grade}"}]}


def grade_decision(state: AgentState) -> str:
    return "generate" if state["retrieval_grade"] in ("sufficient", "partial") else "fallback"


async def generate_node(state: AgentState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "stage": "generating"})
    docs = state["retrieved_docs"][:TOP_K]
    lang, emotion = state.get("language", "zh"), state.get("emotion", "neutral")

    snippets = "\n\n".join(
        f"[{i}] 《{d['doc_title']}》- {d['section']}\n{d['content']}"
        for i, d in enumerate(docs, 1))
    sys = prompts.GENERATE_SYSTEM
    if state.get("retrieval_grade") == "partial":
        sys += "\n\n" + prompts.GENERATE_PARTIAL_HINT
    user_ctx = (f"用户语言：{_lang_name(lang)}；用户情绪：{emotion}\n\n"
                f"知识库片段：\n{snippets}")

    full = ""
    async for tok in chat_stream(
            [{"role": "system", "content": sys},
             {"role": "user", "content": user_ctx},
             *_recent_messages(state)]):
        full += tok
        writer({"type": "delta", "text": tok})

    # 引用溯源：只保留答案中实际引用到的来源，实时帧 + 随消息持久化（刷新后仍可见）
    cited = [i for i in range(1, len(docs) + 1) if f"[{i}]" in full] or list(range(1, len(docs) + 1))
    citations = [
        {"index": i, "title": docs[i - 1]["doc_title"], "section": docs[i - 1]["section"],
         "snippet": docs[i - 1]["content"][:160]} for i in cited]
    writer({"type": "citation", "items": citations})

    return {"messages": [{"role": "assistant", "content": full,
                          "timeline": state.get("_timeline", []),
                          "citations": citations}],
            "answered_topics": [{"topic": state["_query"], "conclusion": full[:120]}],
            "clarify_count": 0}


async def fallback_node(state: AgentState) -> dict:
    """兜底：改写 query 重试一次 → 仍失败则坦诚未覆盖 + 提议转人工。"""
    writer = get_stream_writer()
    writer({"type": "status", "stage": "retrieving"})
    rewritten = (await chat_completion(
        [{"role": "system", "content": prompts.REWRITE_SYSTEM},
         {"role": "user", "content": state["_query"]}], temperature=0)).strip()
    writer({"type": "thinking", "text": f"检索未命中，改写重试：{rewritten}"})
    timeline = state.get("_timeline", []) + [
        {"kind": "thinking", "text": f"检索未命中，改写重试：{rewritten}"}]

    docs = await _do_search(rewritten)
    grade = await _grade(docs, state.get("product_line", "unknown"), rewritten)
    if grade in ("sufficient", "partial"):
        return await generate_node({**state, "retrieved_docs": docs,
                                    "retrieval_grade": grade, "_query": rewritten,
                                    "_timeline": timeline})

    # 坦诚未覆盖，绝不编造（反幻觉红线）
    lang = state.get("language", "zh")
    if lang == "zh":
        answer = ("抱歉，这个问题超出了我当前知识库的覆盖范围，为避免给您不准确的信息，"
                  "我不能直接作答。建议转接人工客服为您跟进，我可以先帮您创建一个工单，"
                  "点击下方按钮即可提交。")
        suggest = ["转人工并创建工单"]
    else:
        answer = ("Sorry, this question is beyond my current knowledge base. To avoid giving "
                  "you inaccurate information, I won't guess. I'd recommend escalating to a "
                  "human agent — you can create a support ticket with the button below.")
        suggest = ["Escalate & create a ticket"]
    writer({"type": "delta", "text": answer})
    writer({"type": "suggest", "items": suggest, "action": "ticket"})
    return {"messages": [{"role": "assistant", "content": answer,
                          "timeline": timeline,
                          "suggests": suggest, "suggest_action": "ticket"}],
            "clarify_count": 0}


# ────────────────────── 内部工具 ──────────────────────

async def _do_search(query: str) -> list[dict]:
    emb = (await embed_texts([query]))[0]
    docs = await hybrid_search(_pool, query, emb, top_k=TOP_K)
    for d in docs:
        d["score"] = float(d["score"])
    return docs


async def _grade(docs: list[dict], product_line: str, query: str) -> str:
    """两级判据：① 产品线元数据校验（先行，零成本）；② 分数短路；③ LLM 评估。"""
    if not docs:
        return "none"
    # ① 产品线校验：用户产品线明确时，过滤不匹配的命中，防"词面相近误判"
    if product_line != "unknown":
        matched = [d for d in docs if d["product_line"] == product_line]
        if not matched:
            return "none"
        docs = matched
    # ② 规则短路：双榜第一的强信号直接 sufficient，省一次 LLM 调用
    if docs[0]["score"] >= SUFFICIENT_SCORE:
        return "sufficient"
    # ③ LLM 评估
    snippets = "\n\n".join(f"[{i}] {d['content'][:300]}" for i, d in enumerate(docs, 1))

    class GradeResult(BaseModel):
        grade: Literal["sufficient", "partial", "none"]

    result = await structured_output(
        [{"role": "system", "content": prompts.GRADE_SYSTEM},
         {"role": "user", "content": f"用户问题：{query}\n\n知识库片段：\n{snippets}"}],
        GradeResult)
    return result.grade


def _quick_questions(lang: str) -> list[str]:
    if lang == "en":
        return ["Battery not charging", "Warranty policy", "Wi-Fi keeps dropping"]
    return ["电池充不进电怎么办", "保修政策咨询", "Wi-Fi 频繁掉线"]
