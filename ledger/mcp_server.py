"""Zero-dependency Model Context Protocol (MCP) server over stdio JSON-RPC 2.0."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys

from .claims import audit_claims
from .cost import append_cost_entry, summarize_case_cost
from .dashboard import write_dashboard
from .economics import evaluate_economics
from .kb import ingest_case_into_kb, search_kb
from .review import render
from .router import route_next_step
from .store import dumps, load
from .table import summarize_rates

SERVER_INFO = {"name": "decision-ledger", "version": "2.0.0"}

TOOLS = [
    {
        "name": "ledger_status",
        "description": "Read case checkpoint, registered sources, draft SHA, approved revision, and transitive evidence impact.",
        "inputSchema": {
            "type": "object",
            "properties": {"case": {"type": "string", "description": "Path to case directory (e.g. .cases/retail)"}},
            "required": ["case"],
        },
    },
    {
        "name": "ledger_route",
        "description": "Select the optimal next workflow process, tool, skill, subagent, model tier, and error-recovery fallback.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string"},
                "budget": {"type": "string", "enum": ["lean", "balanced", "thorough"], "default": "balanced"},
                "failed_tool": {"type": "string"},
                "error_reason": {"type": "string"},
            },
            "required": ["case"],
        },
    },
    {
        "name": "ledger_rates",
        "description": "Compute auditable cohort rates, 95% Wilson confidence intervals, guardrail checks, and Simpson reversal alerts from CSV.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "group": {"type": "string"},
                "segment": {"type": "string"},
                "denominator": {"type": "string"},
                "numerators": {"type": "array", "items": {"type": "string"}},
                "guardrails": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["file", "group", "denominator", "numerators"],
        },
    },
    {
        "name": "ledger_economics",
        "description": "Evaluate product unit economics, contribution margin, cost headroom, payback, and stress scenarios.",
        "inputSchema": {
            "type": "object",
            "properties": {"spec": {"type": "object"}},
            "required": ["spec"],
        },
    },
    {
        "name": "ledger_audit_claims",
        "description": "Check whether communication promises match product scope, unknowns, synthetic-evidence rules, and experiment stop-rules.",
        "inputSchema": {
            "type": "object",
            "properties": {"case": {"type": "string"}},
            "required": ["case"],
        },
    },
    {
        "name": "ledger_kb_search",
        "description": "Search cross-session knowledge base with epistemic guardrails (primary_evidence vs synthetic_probe vs prior_conclusion).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kb_dir": {"type": "string", "default": ".kb"},
                "query": {"type": "string", "default": ""},
                "tag": {"type": "string"},
                "origin_class": {"type": "string", "enum": ["primary_evidence", "synthetic_probe", "prior_conclusion"]},
                "include_inactive": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "ledger_kb_ingest",
        "description": "Index active case sources and approved conclusions into the searchable knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string"},
                "kb_dir": {"type": "string", "default": ".kb"},
            },
            "required": ["case"],
        },
    },
    {
        "name": "ledger_view",
        "description": "Render approved decision or current draft as Markdown, or generate the interactive HTML dashboard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string"},
                "draft": {"type": "boolean", "default": False},
                "decision": {"type": "integer"},
                "dashboard": {"type": "boolean", "default": False},
            },
            "required": ["case"],
        },
    },
    {
        "name": "ledger_cost",
        "description": "Inspect or append a step to .cases/<case>/cost.jsonl with cited model pricing, retries, and human minutes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case": {"type": "string"},
                "step": {"type": "string"},
                "session": {"type": "string", "default": "session-1"},
                "model": {"type": "string", "default": "zero_llm"},
                "input_tokens": {"type": "integer", "default": 0},
                "cached_tokens": {"type": "integer", "default": 0},
                "output_tokens": {"type": "integer", "default": 0},
                "wall_seconds": {"type": "number", "default": 0.0},
                "retries": {"type": "integer", "default": 0},
                "human_minutes": {"type": "number", "default": 0.0},
                "note": {"type": "string", "default": ""},
            },
            "required": ["case"],
        },
    },
]


def _dispatch_tool(name: str, arguments: dict, *, source_health_fn, decision_status_fn):
    if name == "ledger_rates":
        return summarize_rates(
            Path(arguments["file"]).resolve(),
            group=arguments["group"],
            segment=arguments.get("segment"),
            denominator=arguments["denominator"],
            numerators=list(arguments["numerators"]),
            ci=True,
            guardrails=arguments.get("guardrails"),
        )
    if name == "ledger_economics":
        return evaluate_economics(arguments["spec"])
    if name == "ledger_kb_search":
        kb_dir = Path(arguments.get("kb_dir", ".kb")).resolve()
        return search_kb(
            kb_dir,
            arguments.get("query", ""),
            tag=arguments.get("tag"),
            origin_class=arguments.get("origin_class"),
            include_inactive=bool(arguments.get("include_inactive", False)),
        )

    case = Path(arguments["case"]).resolve()
    if name == "ledger_cost":
        if arguments.get("step"):
            return append_cost_entry(
                case,
                step=arguments["step"],
                session=arguments.get("session", "session-1"),
                model=arguments.get("model", "zero_llm"),
                input_tokens=int(arguments.get("input_tokens", 0)),
                cached_tokens=int(arguments.get("cached_tokens", 0)),
                output_tokens=int(arguments.get("output_tokens", 0)),
                wall_seconds=float(arguments.get("wall_seconds", 0.0)),
                retries=int(arguments.get("retries", 0)),
                human_minutes=float(arguments.get("human_minutes", 0.0)),
                note=arguments.get("note", ""),
            )
        with load(case, write=False) as (state, _):
            return summarize_case_cost(case, state.get("telemetry", []))

    with load(case, write=False) as (state, _):
        sources = source_health_fn(case, state["sources"])
        if name == "ledger_status":
            return decision_status_fn(state, sources, date.today().isoformat(), case)
        if name == "ledger_route":
            return route_next_step(
                case,
                state,
                sources,
                budget=arguments.get("budget", "balanced"),
                failed_tool=arguments.get("failed_tool"),
                error_reason=arguments.get("error_reason"),
            )
        if name == "ledger_audit_claims":
            target = state["draft"] or (state["decisions"][-1]["proposal"] if state["decisions"] else None)
            if not target:
                raise ValueError("Case has neither draft nor approved decision to audit")
            return audit_claims(target, sources)
        if name == "ledger_kb_ingest":
            kb_dir = Path(arguments.get("kb_dir", ".kb")).resolve()
            return ingest_case_into_kb(kb_dir, case, state)
        if name == "ledger_view":
            if arguments.get("dashboard"):
                out = write_dashboard(case, state, sources)
                return {"dashboard": str(out)}
            decisions = state["decisions"]
            draft = state["draft"]
            if arguments.get("draft") or (arguments.get("decision") is None and not decisions):
                if not draft:
                    raise ValueError("No draft")
                return render(draft, state["sources"])
            rev = arguments.get("decision") or len(decisions)
            if not 1 <= rev <= len(decisions):
                raise ValueError("Decision revision does not exist")
            entry = decisions[rev - 1]
            return render(entry["proposal"], state["sources"], entry)

    raise ValueError(f"Unknown tool: {name}")


def _read_message(instream) -> tuple[dict | None, str]:
    """Read a JSON-RPC message supporting both newline-delimited JSON and Content-Length framing."""
    while True:
        line = instream.readline()
        if not line:
            return None, "ndjson"
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            while True:
                hdr = instream.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
            body = instream.read(length)
            return json.loads(body.decode("utf-8")), "framed"
        return json.loads(stripped.decode("utf-8")), "ndjson"


def _write_message(outstream, payload: dict, mode: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if mode == "framed":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        outstream.write(header + encoded)
    else:
        outstream.write(encoded + b"\n")
    outstream.flush()


def serve_mcp(*, source_health_fn, decision_status_fn) -> int:
    instream = sys.stdin.buffer
    outstream = sys.stdout.buffer

    while True:
        try:
            message, mode = _read_message(instream)
        except (ValueError, OSError) as error:
            _write_message(
                outstream,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}},
                "ndjson",
            )
            return 2

        if message is None:
            return 0

        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params") or {}

        # Notifications do not have an id and expect no response.
        if "id" not in message:
            continue

        if method == "initialize":
            protocol = params.get("protocolVersion", "2024-11-05")
            result = {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }
            _write_message(outstream, {"jsonrpc": "2.0", "id": msg_id, "result": result}, mode)
        elif method == "ping":
            _write_message(outstream, {"jsonrpc": "2.0", "id": msg_id, "result": {}}, mode)
        elif method == "tools/list":
            _write_message(outstream, {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}, mode)
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                output = _dispatch_tool(
                    tool_name,
                    arguments,
                    source_health_fn=source_health_fn,
                    decision_status_fn=decision_status_fn,
                )
                text = output if isinstance(output, str) else dumps(output)
                _write_message(
                    outstream,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"content": [{"type": "text", "text": text}], "isError": False},
                    },
                    mode,
                )
            except Exception as error:  # noqa: BLE001
                _write_message(
                    outstream,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": f"ledger error: {error}"}],
                            "isError": True,
                        },
                    },
                    mode,
                )
        else:
            _write_message(
                outstream,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                },
                mode,
            )
