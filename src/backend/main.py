# backend/main.py
import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Tuple, TypedDict, Callable

from langgraph.graph import StateGraph, END
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

import sys, os
from pathlib import Path

# Add src root to the Python path
SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(SRC_ROOT))

from milestone1_sitesourcing_langgraph_real import (
    SGState,
    input_parser,
    ideation_node,
    zoning_ranker,
    infrastructure_ranker,
    labor_market_ranker,
    report_aggregator,
)


app = FastAPI(title="Site Sourcing Agent — Milestone 2", version="1.0")
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

@app.get("/")
async def root():
    return RedirectResponse(url="/ui")

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
        loop = asyncio.get_running_loop()
        asyncio.create_task(manager.broadcast(message))
    except RuntimeError:
        asyncio.run(manager.broadcast(message))

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

from pydantic import BaseModel
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
            final_state = app_graph.invoke(init)
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
