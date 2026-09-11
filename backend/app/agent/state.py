from typing import Annotated, List, Literal, Optional
import operator
from typing_extensions import TypedDict

class AgentState(TypedDict):
    # --- User Input ---
    task_description: str

    # --- Intent & Safety ---
    intent: Literal["coding_task", "general_question", "unsafe_request", "unknown"]
    safety_violation_reason: Optional[str]

    # --- Planning & Coding ---
    plan: List[str]
    current_step_index: int
    current_code: str

    # --- Testing & Security ---
    security_violation: Optional[str]
    execution_result: Optional[str]
    execution_error: Optional[str]

    # --- Loop Control ---
    review_attempts: int

    # --- Final Output ---
    final_answer: Optional[str]

    # --- Chat History ---
    messages: Annotated[List[dict], operator.add]

    # --- Status ---
    status: str


def get_initial_state(task_description: str) -> "AgentState":
    """Factory for a clean, fully-initialized state.
    WHY: Guarantees every field exists from step one — no KeyErrors mid-run,
    and Pylance can type-check any dict we pass to a node."""
    return {
        "task_description": task_description,
        "intent": "unknown",
        "safety_violation_reason": None,
        "plan": [],
        "current_step_index": 0,
        "current_code": "",
        "security_violation": None,
        "execution_result": None,
        "execution_error": None,
        "review_attempts": 0,
        "final_answer": None,
        "messages": [],
        "status": "started",
    }