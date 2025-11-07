import asyncio
import json
import threading
import time
from typing import Any, Dict, List, TypedDict

from langgraph.graph import StateGraph, END
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from tools_registry import HAS_OPENAI

load_dotenv()

# new agent-plan creation (dynamic)
from spec import WorkflowSpec
from generator import generate_spec
from dynamic_graph import build_graph_from_spec

import sys, os
from pathlib import Path

# Add src root to the Python path
SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(SRC_ROOT))

# static (milestone 1/2) real-API graph
from milestone1_sitesourcing_langgraph_real import (
    SGState,
    input_parser,
    ideation_node,
    zoning_ranker,
    infrastructure_ranker,
    labor_market_ranker,
    report_aggregator,
)

app = FastAPI(title="Site Sourcing Agent — Milestones 2/3", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/ui",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "web"), html=True),
    name="ui",
)

@app.get("/api/health")
def api_health():
    return {"has_openai": HAS_OPENAI}

@app.get("/")
async def root():
    return RedirectResponse(url="/ui")

# ---------------- Connection manager ----------------

class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active.append(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active:
                self.active.remove(websocket)

    async def broadcast(self, message: dict):
        async with self._lock:
            conns = list(self.active)
        dead = []
        for ws in conns:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for d in dead:
                    if d in self.active:
                        self.active.remove(d)

manager = ConnectionManager()

def emit(message: dict):
    print("[EMIT]", message)
    try:
        asyncio.get_running_loop()
        asyncio.create_task(manager.broadcast(message))
    except RuntimeError:
        asyncio.run(manager.broadcast(message))

# ---------------- Dynamic endpoints (Milestone 3) ----------------

class GeneratePayload(BaseModel):
    description: str

@app.post("/api/generate")
def api_generate(payload: GeneratePayload):
    spec = generate_spec(payload.description)
    return spec.model_dump()

class ExecuteGeneratedPayload(BaseModel):
    prompt: str
    spec: WorkflowSpec

@app.post("/api/execute_generated")
def api_execute_generated(payload: ExecuteGeneratedPayload):
    app_graph = build_graph_from_spec(payload.spec)
    init = {"prompt": payload.prompt, "report_md": None}

    def run():
        emit({"type": "run_start", "ts": time.time(), "mode": "dynamic-invoke"})
        report_md = None  # <-- ensure it's defined in this scope
        try:
            # Get final state deterministically
            final_state = app_graph.invoke(init) or {}

            # 1) Prefer report_md set by drafting wrapper
            report_md = final_state.get("report_md")

            # 2) Fallback: try drafting node's own text
            if not report_md:
                drafting = payload.spec.drafting_node
                draft = final_state.get(drafting)
                if isinstance(draft, dict) and "text" in draft:
                    report_md = draft["text"]
                elif isinstance(draft, str):
                    report_md = draft

            if not report_md:
                report_md = "(no report produced)"

            # Collect per-node provenance AFTER we have final_state
            node_meta = {}
            for n in payload.spec.nodes:
                out = final_state.get(n.id)
                if isinstance(out, dict) and "meta" in out:
                    node_meta[n.id] = out["meta"]

            emit({
                "type": "result_final",
                "report_md": report_md,
                "meta": node_meta,
                "ts": time.time()
            })
            emit({"type": "run_end", "ts": time.time()})

        except Exception as e:
            # If anything failed before report_md was set, it still exists (None)
            emit({"type": "run_error", "error": str(e), "ts": time.time()})

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started"}

# ---------------- Static endpoints (Milestones 1/2) ----------------

def with_events(name: str, fn):
    def wrapped(state: SGState) -> SGState:
        emit({"type": "node_start", "node": name, "ts": time.time()})
        out = fn(state)
        emit({"type": "node_end", "node": name, "ts": time.time(), "writes": list(out.keys())})
        if "report_md" in out:
            emit({"type": "result", "node": name, "report_md": out["report_md"], "ts": time.time()})
        return out
    return wrapped

def build_graph_with_events():
    g = StateGraph(SGState)
    g.add_node("input_parser", with_events("input_parser", input_parser))
    g.add_node("ideation", with_events("ideation", ideation_node))
    g.add_node("zoning_ranker", with_events("zoning_ranker", zoning_ranker))
    g.add_node("infra_ranker", with_events("infrastructure_ranker", infrastructure_ranker))
    g.add_node("labor_ranker", with_events("labor_market_ranker", labor_market_ranker))
    g.add_node("report", with_events("report_aggregator", report_aggregator))

    g.set_entry_point("input_parser")
    g.add_edge("input_parser", "ideation")
    g.add_edge("ideation", "zoning_ranker")
    g.add_edge("ideation", "infra_ranker")
    g.add_edge("ideation", "labor_ranker")
    g.add_edge("zoning_ranker", "report")
    g.add_edge("infra_ranker", "report")
    g.add_edge("labor_ranker", "report")
    g.add_edge("report", END)
    return g.compile()

app_graph = build_graph_with_events()

class ExecutePayload(BaseModel):
    prompt: str
    location: str | None = None
    max_candidates: int | None = 8

@app.get("/api/dag")
async def dag():
    return JSONResponse({
        "nodes": [
            {"id": "input_parser", "label": "Input Parser", "tasks": ["Parse prompt/location", "Geocode (Nominatim)"]},
            {"id": "ideation", "label": "Ideation", "tasks": ["Overpass landuse=industrial", "Deduplicate centroids"]},
            {"id": "zoning_ranker", "label": "Zoning/Access", "tasks": ["Nearest motorway distance", "Proximity curve score"]},
            {"id": "infra_ranker", "label": "Infrastructure", "tasks": ["Power/water/telecom/pipeline scan", "Weighted log score"]},
            {"id": "labor_ranker", "label": "Labor", "tasks": ["FCC County FIPS", "BLS unemployment", "Workforce proximity"]},
            {"id": "report", "label": "Report", "tasks": ["Weighted combine", "LLM draft (Markdown)"]},
        ],
        "edges": [
            ["input_parser", "ideation"],
            ["ideation", "zoning_ranker"],
            ["ideation", "infra_ranker"],
            ["ideation", "labor_ranker"],
            ["zoning_ranker", "report"],
            ["infra_ranker", "report"],
            ["labor_ranker", "report"],
        ]
    })

@app.post("/api/execute")
async def execute(payload: ExecutePayload):
    init: SGState = {"prompt": payload.prompt}
    if payload.location:
        init["location"] = payload.location
    if payload.max_candidates:
        init["max_candidates"] = payload.max_candidates

    def run():
        try:
            emit({"type": "run_start", "ts": time.time(), "init": init})
            _ = app_graph.invoke(init)
            emit({"type": "run_end", "ts": time.time()})
        except Exception as e:
            emit({"type": "run_error", "error": str(e), "ts": time.time()})

    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"status": "started"})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)
