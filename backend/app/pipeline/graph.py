"""LangGraph wiring of the pipeline (ADR-003).

plan -> gather -> extract -> build_graph -> resolve_conflicts -> cluster
     -> consensus -> critic --(retry)--> gather
                            --(else)---> answer -> verify -> END
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.pipeline import nodes
from app.pipeline.context import PipelineContext
from app.pipeline.state import RunState

NODE_ORDER = [
    "plan",
    "gather",
    "extract",
    "build_graph",
    "resolve_conflicts",
    "cluster",
    "consensus",
    "critic",
]

_NODE_FUNCTIONS: dict[str, Callable[[RunState, PipelineContext], dict]] = {
    "plan": nodes.plan_node,
    "gather": nodes.gather_node,
    "extract": nodes.extract_node,
    "build_graph": nodes.build_graph_node,
    "resolve_conflicts": nodes.resolve_node,
    "cluster": nodes.cluster_node,
    "consensus": nodes.consensus_node,
    "critic": nodes.critic_node,
    "answer": nodes.answer_node,
    "verify": nodes.verify_node,
}


def _bind(fn: Callable[[RunState, PipelineContext], dict], ctx: PipelineContext):  # noqa: ANN202
    def node(state: RunState) -> dict:
        return fn(state, ctx)

    node.__name__ = fn.__name__
    return node


def build_pipeline(ctx: PipelineContext):  # noqa: ANN201 - LangGraph CompiledStateGraph
    graph = StateGraph(RunState)
    for name, fn in _NODE_FUNCTIONS.items():
        graph.add_node(name, _bind(fn, ctx))
    graph.add_edge(START, NODE_ORDER[0])
    for current, following in zip(NODE_ORDER, NODE_ORDER[1:], strict=False):
        graph.add_edge(current, following)
    graph.add_conditional_edges(
        "critic", nodes.route_after_critic, {"gather": "gather", "answer": "answer"}
    )
    graph.add_edge("answer", "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def initial_state(question: str) -> RunState:
    return {"question": question, "caveats": [], "iteration": 0}
