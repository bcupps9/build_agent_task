# src/backend/generator.py
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import SystemMessage, HumanMessage
from spec import WorkflowSpec

_gen_model = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
_parser = JsonOutputParser(pydantic_object=WorkflowSpec)

SYSTEM = """You convert workflow descriptions into executable DAGs.
Output must be STRICT JSON that matches the provided schema.
Rules:
- 3–7 nodes, each with distinct purpose.
- Each node MUST have: id, label, prompt, tasks (bullet-like), tools (names from ToolRegistry).
- Use 'llm' for tools unless otherwise stated.
- Include a 'drafting_node' that composes a final report.
- Edges must form an acyclic flow. Allow parallel branches.
"""

USER_TMPL = """Description:
{desc}

Schema (pydantic):
{schema}

Return ONLY JSON that validates to this schema."""

def generate_spec(description: str) -> WorkflowSpec:
    msg = USER_TMPL.format(desc=description, schema=WorkflowSpec.model_json_schema())
    out = _gen_model.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=msg)])
    parsed = _parser.parse(out.content)
    spec = WorkflowSpec(**parsed) if isinstance(parsed, dict) else parsed
    # --- normalize: ensure drafting_node is one of the node ids ---
    node_ids = {n.id for n in spec.nodes}
    if spec.drafting_node not in node_ids:
        # pick the last node as drafting fallback (or any deterministic choice)
        fallback = spec.nodes[-1].id if spec.nodes else "report"
        spec.drafting_node = fallback
    return spec

