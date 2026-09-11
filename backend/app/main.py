from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router as agent_router

app = FastAPI(title="AI Software Engineer (Devin-Style)",
              description="Autonomous LangGraph Agent with E28 Sandbox",
              version="1.0.0"
              )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, change "*" to "https://your-frontend-domain.com"
    allow_credentials=True,
    allow_methods=["*"],  # Allows GET, POST, etc.
    allow_headers=["*"],  # Allows Content-Type, Authorization, etc.
)

app.include_router(agent_router, prefix="/api", tags=["agent"])

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "AI Software Engineer Agent Backend"}