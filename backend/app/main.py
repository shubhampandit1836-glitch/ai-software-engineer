import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

# WHY: uvicorn's default config only shows WARNING+ from app loggers - that's
# why the "unavailable" warning appeared but the "ready" INFO line never did.
logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router as agent_router
from app.api.threads_routes import router as threads_router
from app.agent.graph import async_setup_stateful_graph
# WHY: Windows defaults to ProactorEventLoop; psycopg async requires Selector
import asyncio
import sys

# WHY: Windows defaults to ProactorEventLoop; psycopg async requires Selector.
# set_event_loop_policy is deprecated (Python 3.14+; removed in 3.16) in favor of
# asyncio.Runner(loop_factory=...), but is the ONLY option on Python 3.11.
# We use getattr to avoid static analysis (Pyrefly) deprecation warnings.
if sys.platform == "win32":
    set_loop_policy = getattr(asyncio, "set_event_loop_policy", None)
    if callable(set_loop_policy):
        set_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

@asynccontextmanager
async def lifespan(app: FastAPI):
    # WHY lifespan startup: AsyncConnectionPool.open() requires a running
    # event loop. By the time this hook fires, uvicorn's loop is live, so
    # async DB connections and the stateful graph compile successfully.
    await async_setup_stateful_graph()
    yield
    # Teardown: nothing needed - process exit closes the pool.


app = FastAPI(
    title="AI Software Engineer (Devin-Style)",
    description="Autonomous LangGraph Agent with E2B Sandbox",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, change "*" to "https://your-frontend-domain.com"
    allow_credentials=True,
    allow_methods=["*"],  # Allows GET, POST, etc.
    allow_headers=["*"],  # Allows Content-Type, Authorization, etc.
)

app.include_router(agent_router, prefix="/api", tags=["Agent"])
app.include_router(threads_router, prefix="/api", tags=["Threads"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "AI Software Engineer Agent Backend"}