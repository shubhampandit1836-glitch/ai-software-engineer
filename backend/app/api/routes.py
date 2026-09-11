import json
from typing import Any, AsyncGenerator, Dict
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.agent.graph import agent_graph, RECURSION_LIMIT
from app.agent.state import get_initial_state

router = APIRouter()


class TaskRequest(BaseModel):
    task: str


def _build_payload(node_name: str, node_output: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the JSON payload for one SSE event.

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
        async for event in agent_graph.astream(  # type: ignore
            initial_state,
            stream_mode="updates",
            config={"recursion_limit": RECURSION_LIMIT},
        ):
            for node_name, node_output in event.items():
                # WHY: Defensive guard - 'updates' mode should always give
                # us dicts, but a non-dict would crash json.loads downstream.
                if not isinstance(node_output, dict):
                    continue

                payload = _build_payload(node_name, node_output)
                # WHY: default=str is a safety net - if any field ever
                # contains a non-serializable object, it degrades to a
                # string instead of killing the stream.
                yield f"data: {json.dumps(payload, default=str)}\n\n"

        # WHY: THE ONLY completion event - deliberately outside and after
        # the loop. One stream, one end. The indentation-paste bug from v1
        # is now structurally impossible.
        yield f"data: {json.dumps({'node': 'end', 'status': 'completed'})}\n\n"

    except Exception as e:
        error_payload = {
            "node": "error",
            "status": "failed",
            "execution_error": f"Agent runtime error: {str(e)}",
        }
        yield f"data: {json.dumps(error_payload)}\n\n"


@router.post("/agent/stream")
async def run_agent_stream(request: TaskRequest):
    """HTTP POST endpoint that initiates streaming agent execution."""
    return StreamingResponse(
        stream_agent_execution(request.task),
        media_type="text/event-stream",
    )