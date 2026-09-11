from typing import Annotated, List, Literal, Optional
import operator
from typing_extensions import NotRequired, Required, TypedDict

class AgentState(TypedDict, total=False):
    # --- User Input ---
    task_description: Required[str]

    # --- Intent & Safety ---
    intent: NotRequired[Literal["coding_task", "general_question", "unsafe_request", "unknown"]]
    safety_violation_reason: NotRequired[Optional[str]]

    # --- Planning & Coding ---
    plan: NotRequired[List[str]]
    current_step_index: NotRequired[int]
    current_code: NotRequired[str]

    # --- Testing & Security ---
    security_violation: NotRequired[Optional[str]]
    execution_result: NotRequired[Optional[str]]
    execution_error: NotRequired[Optional[str]]

    # --- Loop Control ---
    review_attempts: NotRequired[int]

    # --- Final Output ---
    final_answer: NotRequired[Optional[str]]

    # --- Chat History ---
    messages: NotRequired[Annotated[List[dict], operator.add]]

    # --- Status ---
    status: NotRequired[str]
    
def get_initial_state(task_description: str) -> "AgentState":
    """Factory for a clean, fully-initialized state.
    WHY: Guarantees every field exists from step one — no KeyErrors mid-run,
    and Pylance can type-check any dict we pass to a node."""
    return AgentState(
        task_description=task_description,
        intent="unknown",
        safety_violation_reason=None,
        plan=[],
        current_step_index=0,
        current_code="",
        security_violation=None,
        execution_result=None,
        execution_error=None,
        review_attempts=0,
        final_answer=None,
        messages=[],
        status="started",
    )