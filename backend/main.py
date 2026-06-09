"""
LogSense — FastAPI backend.

Endpoints:
  POST /chat                    — conversational agent
  GET  /ws/{session_id}         — WebSocket for watchdog alerts
  POST /blast-radius            — calculate business impact for a time window
  POST /postmortem              — generate post-mortem document
  POST /data/generate           — (re)generate and ingest synthetic data
  GET  /health                  — liveness check
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.runner import chat as agent_chat, shutdown as agent_shutdown
from features import watchdog, blast_radius, postmortem
from elastic.client import close_es


# ── lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start watchdog in the background
    watchdog_task = asyncio.create_task(watchdog.run_watchdog())
    yield
    watchdog_task.cancel()
    await agent_shutdown()
    await close_es()


app = FastAPI(title="LogSense", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket manager ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws

        async def push(alert: dict):
            try:
                await ws.send_text(json.dumps(alert))
            except Exception:
                pass

        watchdog.subscribe(push)
        self._connections[f"{session_id}_cb"] = push  # keep ref

    def disconnect(self, session_id: str):
        cb = self._connections.pop(f"{session_id}_cb", None)
        self._connections.pop(session_id, None)
        if cb:
            watchdog.unsubscribe(cb)


manager = ConnectionManager()


# ── routes ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = "default"

class ChatResponse(BaseModel):
    session_id: str
    response: str


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    full_response = ""
    async for chunk in agent_chat(req.message, session_id, req.user_id):
        full_response += chunk
    return ChatResponse(session_id=session_id, response=full_response)


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await manager.connect(session_id, websocket)
    try:
        while True:
            # Keep connection alive; client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id)


class BlastRadiusRequest(BaseModel):
    start: str             # ISO-8601 e.g. "2026-06-09T14:52:00Z"
    end: str
    affected_services: list[str] = []

@app.post("/blast-radius")
async def blast_radius_endpoint(req: BlastRadiusRequest):
    try:
        result = await blast_radius.calculate(req.start, req.end, req.affected_services)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PostmortemRequest(BaseModel):
    conversation: list[dict]        # [{"role": "user"|"assistant", "content": "..."}]
    blast_radius: dict | None = None
    incident_start: str = ""
    incident_end: str = ""
    severity: str = "P1"

@app.post("/postmortem")
async def postmortem_endpoint(req: PostmortemRequest):
    try:
        doc = await postmortem.generate(
            conversation=req.conversation,
            blast_radius=req.blast_radius,
            incident_start=req.incident_start,
            incident_end=req.incident_end,
            severity=req.severity,
        )
        return {"markdown": doc}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/data/generate")
async def generate_data():
    """Regenerate and ingest synthetic data (blocking — may take ~60s)."""
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "data/generate.py"],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr)
        return {"status": "ok", "output": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
