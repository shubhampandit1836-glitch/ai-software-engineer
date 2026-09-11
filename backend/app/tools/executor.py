import os
import asyncio
from langchain_core.tools import tool
from e2b_code_interpreter import Sandbox
from dotenv import load_dotenv

load_dotenv()

def _run_sync_code(code: str, api_key: str) -> str:
    """
    Synchronous function to run in a separate thread.
    """
    sandbox = None
    try:
        # Initialize the sync sandbox
        sandbox = Sandbox(api_key=api_key) # type: ignore
        execution = sandbox.run_code(code) # type: ignore
        
        if execution.error:
            return f"Execution Error:\n{execution.error.name}: {execution.error.value}"
        
                # WHY: E2B v1.0+ sometimes stores print() outputs in logs.stdout 
        # rather than the .text property. We check both to be bulletproof.
        output = execution.text
        if not output and hasattr(execution, 'logs') and execution.logs.stdout:
            output = "\n".join(execution.logs.stdout)
            
        if not output:
            return "Code executed successfully, but produced no text output."
            
        return f"Execution Output:\n{output}"
        
    except Exception as e:
        return f"Sandbox Connection Error: {str(e)}"
    finally:
        # WHY: E2B v1.0 changed .close() to .kill(). 
        # We use hasattr() to make this code bulletproof. It will work regardless 
        # of whether the user has v0.9 or v1.0+ installed. Pylance won't complain either.
        if sandbox:
            if hasattr(sandbox, 'kill'):
                sandbox.kill() # type: ignore
            elif hasattr(sandbox, 'close'):
                sandbox.close() # type: ignore

@tool
async def execute_python_code(code: str) -> str:
    """
    Executes Python code in a secure cloud sandbox and returns the output or error.
    """
    e2b_api_key = os.getenv("E2B_API_KEY")
    if not e2b_api_key:
        return "Error: E2B_API_KEY not found in environment variables."

    # WHY: Run the blocking sync code in a background thread to keep FastAPI async
    result = await asyncio.to_thread(_run_sync_code, code, e2b_api_key)
    return result