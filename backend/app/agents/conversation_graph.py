"""One inbound reply per invocation; SQL and memory retain the conversation."""
from typing import TypedDict
from langgraph.graph import START, END, StateGraph


class ConversationState(TypedDict, total=False):
    message_id: str
    body: str
    direction: str
    stop_reason: str
    classification: str


def build_conversation_graph(detect_stop, suppress, analyze):
    graph = StateGraph(ConversationState)
    graph.add_node("detect_stop", detect_stop)
    graph.add_node("suppress", suppress)
    graph.add_node("analyze", analyze)
    graph.add_edge(START, "detect_stop")
    graph.add_conditional_edges("detect_stop", lambda state: "suppress" if state.get("stop_reason") else "analyze",
                                {"suppress": "suppress", "analyze": "analyze"})
    graph.add_edge("suppress", END)
    graph.add_edge("analyze", END)
    return graph.compile()
