# src/backend/spec.py
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Tuple, Optional

class ToolSpec(BaseModel):
    name: str = Field(..., description="Tool id from ToolRegistry, e.g. 'llm' or 'geocode'")
    params: Dict[str, Any] | None = None

class NodeSpec(BaseModel):
    id: str
    label: str
    role: str = "worker"
    prompt: str = Field(..., description="Instruction the node sends to its tool(s)")
    tasks: List[str] = Field(default_factory=list)
    tools: List[ToolSpec] = Field(default_factory=list)

class WorkflowSpec(BaseModel):
    nodes: List[NodeSpec]
    edges: List[Tuple[str, str]]
    drafting_node: str = Field(..., description="Node id that drafts final report")
