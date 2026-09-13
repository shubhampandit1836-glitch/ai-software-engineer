import json
from typing import Any, AsyncGenerator, Dict
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.agent.graph import agent_graph, RECURSION_LIMIT
from app.agent.state import get_initial_state
from app.tools.sandbox_manager import destroy_sandbox

router = APIRouter()

# WHY: only these nodes stream tokens to the UI. Planner/coder/reviewer
# tokens stay internal (their RESULTS stream as update events). Making it
# a constant means enabling live code-writing later is a one-word change.
STREAMING_NODES = {"direct_answer", "synthesizer"}

class TaskRequest(BaseModel):
    task: str


def _build_payload(node_name: str, node_output: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the JSON payload for one node-update SSE event.

    WHY: We explicitly pick fields instead of dumping the whole node output.
    The 'messages' field holds LangChain BaseMessage objects which are NOT
    JSON-serializable - dumping them would crash the stream mid-run.
    """
    return {
        "node": node_name,
        "status": node_output.get("status"),
        "intent": node_output.get("intent"),
        "plan": node_output.get("plan"),
        "current_step_index": node_output.get("current_step_index"),
        "current_code": node_output.get("current_code"),
        "execution_result": node_output.get("execution_result"),
        "execution_error": node_output.get("execution_error"),
        "security_violation": node_output.get("security_violation"),
        "review_attempts": node_output.get("review_attempts"),
        "final_answer": node_output.get("final_answer"),
    }


async def stream_agent_execution(task_description: str) -> AsyncGenerator[str, None]:
    """Streams LangGraph node updates to the client via Server-Sent Events."""
    # WHY: The factory guarantees all 13 state fields exist - no hand-built
    # dicts that drift out of sync with AgentState.
    initial_state = get_initial_state(task_description)

    try:
        # WHY: TWO stream modes at once. With a list of modes, each yielded
        # item is a (mode, payload) tuple:
        #   "updates"  -> {node_name: node_output}   (pill, steps, final_answer)
        #   "messages" -> (token_chunk, metadata)    (live typing)
        async for event in agent_graph.astream(  # type: ignore
            initial_state,
            stream_mode=["updates", "messages"],
            config={"recursion_limit": RECURSION_LIMIT},
        ):
            # ---------- Token stream ----------
            if not isinstance(event, tuple) or len(event) != 2:
                continue

            mode, payload = event
            if mode == "messages":
                if not isinstance(payload, tuple) or len(payload) != 2:
                    continue

                chunk, meta = payload
                if not isinstance(meta, dict):
                    continue

                node = meta.get("langgraph_node", "")
                if node in STREAMING_NODES:
                    content = getattr(chunk, "content", "")
                    # WHY: content can be a list (multimodal) or empty -
                    # only forward real string tokens.
                    if isinstance(content, str) and content:
                        token_payload = {
                            "node": node,
                            "type": "token",
                            "content": content,
                        }
                        yield f"data: {json.dumps(token_payload)}\n\n"
                continue

            # ---------- Node updates (existing behavior) ----------
            if not isinstance(payload, dict):
                continue

            for node_name, node_output in payload.items():
                if not isinstance(node_output, dict):
                    continue

                node_payload = _build_payload(node_name, node_output)
                yield f"data: {json.dumps(node_payload, default=str)}\n\n"

        # WHY: THE ONLY completion event - outside and after the loop.
        yield f"data: {json.dumps({'node': 'end', 'status': 'completed'})}\n\n"
    except Exception as e:
        error_payload = {
            "node": "error",
            "status": "failed",
            "execution_error": f"Agent runtime error: {str(e)}",
        }
        yield f"data: {json.dumps(error_payload)}\n\n"

    finally:
        # WHY: THE guaranteed cleanup. Every stream exit path - completion,
        # client disconnect, crash - destroys the persistent sandbox. A
        # leaked sandbox burns E2B credits until E2B's own timeout reaps it.
        destroy_sandbox()


@router.post("/agent/stream")
async def run_agent_stream(request: TaskRequest):
    """HTTP POST endpoint that initiates streaming agent execution."""
    return StreamingResponse(
        stream_agent_execution(request.task),
        media_type="text/event-stream",
    )