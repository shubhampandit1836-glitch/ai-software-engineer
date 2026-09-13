from typing import Literal
from langgraph.graph import StateGraph, START, END
from app.agent.state import AgentState
from app.agent.safety import (
    input_guardrail_node,
    intent_classifier_node,
    security_scanner_node,
)
import logging
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.nodes import planner_node, coder_node, tester_node, reviewer_node
from app.agent.answer_nodes import (
    direct_answer_node,
    synthesizer_node,
    failure_answer_node,
)

# WHY: Dead-man switch. After 3 failed fix attempts we stop burning tokens
# and credits, and exit gracefully through failure_answer_node.
MAX_REVIEW_ATTEMPTS = 3

# WHY: Worst-case walk (5 plan steps x 3 nodes + 3 reviews x 3 nodes +
# overhead) exceeds LangGraph's default recursion limit of 25.
# 100 gives headroom without allowing a true runaway.
RECURSION_LIMIT = 100


def route_after_guardrail(state: AgentState) -> Literal["intent_classifier", "__end__"]:
    """Regex guardrail verdict: rejected runs exit immediately with their message."""
    if state.get("status") == "rejected":
        return "__end__"
    return "intent_classifier"


def route_after_intent(state: AgentState) -> Literal["direct_answer", "planner", "__end__"]:
    """Intent verdict: code it, answer it, or stop (LLM-caught unsafe)."""
    # WHY: 'rejected' here means the LLM classifier caught an unsafe request
    # (final_answer already attached in safety.py).
    if state.get("status") == "rejected":
        return "__end__"
    if state.get("intent") == "coding_task":
        return "planner"
    # WHY: general_question AND any unrecognized value take the safe path
    # that never touches the code sandbox. Ambiguity must never reach execution.
    return "direct_answer"


def route_after_security(state: AgentState) -> Literal["tester", "reviewer", "failure_answer"]:
    """Security scan verdict: clean code runs; dangerous code gets one more fix."""
    if state.get("status") == "security_violation":
        if state.get("review_attempts", 0) >= MAX_REVIEW_ATTEMPTS:
            return "failure_answer"
        return "reviewer"
    return "tester"


def route_after_testing(state: AgentState) -> Literal["coder", "reviewer", "synthesizer", "failure_answer"]:
    """Test verdict: next step, fix it, finish, or give up gracefully."""
    status = state.get("status", "")

    # WHY: Sandbox/network failure - the reviewer cannot fix infrastructure.
    if status == "infrastructure_error":
        return "failure_answer"

    if status == "testing_failed":
        if state.get("review_attempts", 0) >= MAX_REVIEW_ATTEMPTS:
            return "failure_answer"
        return "reviewer"

    # testing_passed: any plan steps remaining?
    plan = state.get("plan") or []
    if state.get("current_step_index", 0) < len(plan):
        return "coder"
    return "synthesizer"


def _build_workflow() -> StateGraph:
    """Builds the agent workflow graph WITHOUT compiling it.

    WHY separate from compile: LangGraph's .compile() is a one-way
    transformation - you cannot compile again with a different checkpointer.
    Returning the raw StateGraph lets callers choose their own compile
    options (no checkpointer for stateless use, PostgresSaver for threads).
    """
    workflow = StateGraph(AgentState)

    # --- Nodes ---
    workflow.add_node("input_guardrail", input_guardrail_node)
    workflow.add_node("intent_classifier", intent_classifier_node)
    workflow.add_node("direct_answer", direct_answer_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("security_scanner", security_scanner_node)
    workflow.add_node("tester", tester_node)
    workflow.add_node("reviewer", reviewer_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("failure_answer", failure_answer_node)

    # --- Edges ---
    workflow.add_edge(START, "input_guardrail")

    workflow.add_conditional_edges(
        "input_guardrail",
        route_after_guardrail,
        {"intent_classifier": "intent_classifier", "__end__": "__end__"},
    )

    workflow.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {"direct_answer": "direct_answer", "planner": "planner", "__end__": "__end__"},
    )

    workflow.add_edge("direct_answer", END)

    workflow.add_edge("planner", "coder")
    workflow.add_edge("coder", "security_scanner")

    workflow.add_conditional_edges(
        "security_scanner",
        route_after_security,
        {
            "tester": "tester",
            "reviewer": "reviewer",
            "failure_answer": "failure_answer",
        },
    )

    workflow.add_conditional_edges(
        "tester",
        route_after_testing,
        {
            "coder": "coder",
            "reviewer": "reviewer",
            "synthesizer": "synthesizer",
            "failure_answer": "failure_answer",
        },
    )

    # WHY: fixed code goes back through the SCANNER (not straight to the
    # tester) so a fix can never introduce a new dangerous operation.
    workflow.add_edge("reviewer", "security_scanner")

    workflow.add_edge("synthesizer", END)
    workflow.add_edge("failure_answer", END)

    return workflow


def create_agent_graph():
    """Builds and compiles the agent graph (no checkpointer - stateless)."""
    return _build_workflow().compile()


logger = logging.getLogger(__name__)
agent_graph = create_agent_graph()

# WHY module-level None: the stateful graph is initialized in
# async_setup_stateful_graph() called from FastAPI lifespan, because
# AsyncConnectionPool.open() requires a running event loop. At import
# time no event loop exists yet, so we build the graph skeleton here
# and open the pool connection in the lifespan hook.
agent_graph_stateful = None
_async_checkpoint_pool = None


async def async_setup_stateful_graph() -> None:
    """Opens the async checkpoint pool and compiles the stateful graph.

    Must be called once from the FastAPI lifespan (startup) hook - at that
    point uvicorn's event loop is running, so AsyncConnectionPool.open()
    and any awaitable setup calls succeed.
    """
    global agent_graph_stateful, _async_checkpoint_pool
    try:
        import os
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool, ConnectionPool

        conninfo = os.environ["DATABASE_URL"]
        if "sslmode" not in conninfo:
            conninfo += "?sslmode=require"

        # WHY sync pool for setup only: CREATE INDEX CONCURRENTLY must run
        # in autocommit mode. We open one sync connection, run setup, close
        # immediately - this is the one blocking call we allow at startup.
        _setup_pool = ConnectionPool(
            conninfo=conninfo, min_size=1, max_size=1,
            kwargs={"autocommit": True}, open=True,
        )
        PostgresSaver(_setup_pool).setup()  # type: ignore[arg-type]
        _setup_pool.close()

        # WHY AsyncConnectionPool: ainvoke/astream are async; the sync
        # PostgresSaver raises NotImplementedError on every async call.
        _async_checkpoint_pool = AsyncConnectionPool(
            conninfo=conninfo, min_size=1, max_size=3,
            kwargs={"autocommit": True}, open=False,
        )
        await _async_checkpoint_pool.open()

        _checkpointer = AsyncPostgresSaver(_async_checkpoint_pool)  # type: ignore[arg-type]
        agent_graph_stateful = _build_workflow().compile(checkpointer=_checkpointer)
        logger.info("AsyncPostgresSaver ready - stateful threads enabled.")
    except Exception as e:
        logger.warning(
            "AsyncPostgresSaver unavailable (%s) - threads engine disabled.", e,
        )
        agent_graph_stateful = None