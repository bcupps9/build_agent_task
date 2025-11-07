from typing import Dict, Any, TypedDict, Optional
from langgraph.graph import StateGraph, END
from spec import WorkflowSpec, NodeSpec
from tools_registry import ToolRegistry

class DynamicState(TypedDict, total=False):
    prompt: str
    report_md: Optional[str]

def make_node_fn(node: NodeSpec):
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        ctx = {
            "global_prompt": state.get("prompt"),
            "upstream": {k: v for k, v in state.items() if k not in ("prompt", "report_md")}
        }

        tools = node.tools or []

        # default to 'llm' if none listed (now dev-safe)
        if not tools:
            impl = ToolRegistry.get("llm")
            if not impl:
                return {node.id: {"error": "missing default 'llm' tool"}}
            try:
                result = impl(prompt=node.prompt, context=ctx)
            except Exception as e:
                result = {"error": f"tool 'llm' failed: {e}"}
            return {node.id: result}

        # execute listed tools sequentially
        result = None
        for t in tools:
            tool_name = getattr(t, "name", None)
            impl = ToolRegistry.get(tool_name)
            if not impl:
                result = {"error": f"unknown tool '{tool_name}'"}
                break
            params = (getattr(t, "params", None) or {})
            try:
                result = impl(prompt=node.prompt, context={**ctx, "tool_params": params})
            except Exception as e:
                result = {"error": f"tool '{tool_name}' failed: {e}"}
                break
            ctx["last_result"] = result

        return {node.id: result}
    return fn

def build_graph_from_spec(spec: WorkflowSpec):
    g = StateGraph(DynamicState)

    # add nodes; wrap drafting node to also set report_md
    for n in spec.nodes:
        base_fn = make_node_fn(n)
        if n.id == spec.drafting_node:
            def drafting_wrapper(state, _base_fn=base_fn, _nid=n.id):
                out = _base_fn(state)
                node_out = out.get(_nid, {}) or {}
                text = (
                    node_out.get("text")
                    or node_out.get("markdown")
                    or node_out.get("md")
                    or ""
                )
                return {**out, "report_md": text}
            g.add_node(n.id, drafting_wrapper)
        else:
            g.add_node(n.id, base_fn)

    # edges
    incoming = {n.id: 0 for n in spec.nodes}
    for s, t in spec.edges:
        incoming[t] = incoming.get(t, 0) + 1
        g.add_edge(s, t)

    roots = [nid for nid, deg in incoming.items() if deg == 0] or [spec.nodes[0].id]

    g.add_edge(spec.drafting_node, END)

    if len(roots) == 1:
        g.set_entry_point(roots[0])
    else:
        g.add_node("start", lambda s: s)
        for r in roots:
            g.add_edge("start", r)
        g.set_entry_point("start")

    return g.compile()
