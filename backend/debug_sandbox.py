"""Isolates the v3 tool chain from the agent loop: write -> list -> run.
Run with: uv run python debug_sandbox.py
"""
import asyncio
from app.tools.sandbox_manager import destroy_sandbox
from app.tools.file_tools import write_file, list_files, run_command


async def main():
    print("=" * 60)
    print("TEST 1 - write_file:")
    print(write_file.invoke({"path": "main.py", "content": "print('hello from sandbox')\n"}))
    print("=" * 60)
    print("TEST 2 - list_files:")
    print(list_files.invoke({}))
    print("=" * 60)
    print("TEST 3 - run_command:")
    print(run_command.invoke({"command": "python main.py"}))
    print("=" * 60)
    destroy_sandbox()
    print("sandbox destroyed - done")


asyncio.run(main())