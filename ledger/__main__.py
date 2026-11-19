"""Local CLI and MCP tools for an IDE product-research and strategy agent."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

from .claims import audit_claims, scan_untrusted_text
from .cost import append_cost_entry, import_cost_from_logs, summarize_case_cost
from .dashboard import serve_dashboard, write_dashboard
from .draft_template import compile_draft_template, generate_draft_template
from .economics import evaluate_economics_file, render_assumptions_markdown
from .kb import ingest_case_into_kb, link_entries, retract_entry, search_kb, upsert_entry
from .mcp_server import serve_mcp
from .repo_audit import audit_repository
from .review import impact, render, validate
from .router import record_telemetry, route_next_step, summarize_telemetry
from .store import case_file, digest, dumps, init, load, now
from .strategies import export_strategies
from .table import summarize_rates
from .web_fetch import fetch_open_webpage
from .xlsx import extract_xlsx_text, read_xlsx_records, render_xlsx_markdown

SOURCE_KINDS = ["document", "table", "web", "synthetic", "interview", "ux"]


def source_health(case, records):
    health = {}
    for key, record in records.items():
        version = record["versions"][-1]
        current = {
            **version,
            "kind": record["kind"],
            "url": record["url"],
            "updates": list(record.get("updates") or []),
            "health": "ok",
        }
        if record.get("retired"):
            health[key] = {**current, "health": "retired", "retired": record["retired"]}
            continue
        try:
            current_sha, _ = digest(case_file(case, version["path"]))
            if current_sha != version["sha"]:
                current["health"] = "changed"
        except (OSError, ValueError):
            current["health"] = "unavailable"
        health[key] = current
    return health


def register(args, case, state, db):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.id):
        raise ValueError("Source ID must use lowercase letters, digits, hyphens or underscores")
    if args.kind == "web":
        url = urlsplit(args.url or "")
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or any(char.isspace() or char in "<>()" for char in args.url)
        ):
            raise ValueError("Web snapshots require an HTTPS source URL without credentials")
    elif args.url:
        raise ValueError("--url is reserved for web snapshots")
    path = case_file(case, args.file)
    sha, content = digest(path)
    record = state["sources"].setdefault(args.id, {"kind": args.kind, "url": args.url, "versions": []})
    if (args.kind, args.url) != (record["kind"], record["url"]):
        raise ValueError("Use a new source ID when the type or URL changes")
    updates = [u.strip() for u in (getattr(args, "updates", None) or []) if u and u.strip()]
    if updates:
        merged = sorted(set(record.get("updates") or []) | set(updates))
        record["updates"] = merged
    relative = Path(args.file).as_posix()
    record.pop("retired", None)
    inj_flags = []
    suffix_lower = path.suffix.lower()
    if suffix_lower == ".xlsx":
        try:
            inj_flags = scan_untrusted_text(extract_xlsx_text(content))
        except ValueError:
            inj_flags = []
    elif suffix_lower not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".pdf", ".zip"}:
        try:
            inj_flags = scan_untrusted_text(content.decode("utf-8", errors="ignore"))
        except OSError:
            inj_flags = []
    versions = record["versions"]
    if not versions or (sha, relative) != (versions[-1]["sha"], versions[-1]["path"]):
        db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)", (sha, content))
        ver_entry = {"sha": sha, "path": relative, "at": now()}
        if inj_flags:
            ver_entry["injection_flags"] = inj_flags
        versions.append(ver_entry)
    res = {"id": args.id, **versions[-1]}
    if record.get("updates"):
        res["updates"] = list(record["updates"])
    return res


def outstanding(proposal, sources):
    return any(impact(proposal, sources).values())


def fingerprint(proposal, sequence):
    return hashlib.sha256(dumps([sequence, proposal]).encode("utf-8")).hexdigest()


def decision_status(state, sources, today, case=None):
    history = state["decisions"]
    approved = history[-1] if history else None
    current = approved["proposal"] if approved else None
    draft = state["draft"]
    actions = []
    if approved:
        for action in current["actions"]:
            completion = approved.get("completions", {}).get(action["id"])
            status = "done" if action_done(completion) else ("overdue" if action["due"] < today else "open")
            actions.append({**action, "completion": completion, "status": status})
    res = {
        "request": state["request"],
        "checkpoint": state["checkpoint"],
        "sources": sources,
        "draft": (
            {
                "title": draft["title"],
                "option": draft["recommended"],
                "sha": fingerprint(draft, state.get("draft_sequence", 0)),
            }
            if draft
            else None
        ),
        "draft_impact": impact(draft, sources) if draft else None,
        "approved": (
            {k: v for k, v in approved.items() if k not in {"proposal", "completions"}} | {"actions": actions}
            if approved
            else None
        ),
        "approved_impact": impact(current, sources) if current else None,
        "history": [
            {"revision": entry["revision"], "option": entry["option"], "at": entry["at"]}
            for entry in history
        ],
        "telemetry": summarize_telemetry(state.get("telemetry", [])),
    }
    if case is not None:
        res["cost"] = summarize_case_cost(case, state.get("telemetry", []))
    return res


def action_done(completion):
    return bool(completion)


def parse_args():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    def command(name, description):
        parser = sub.add_parser(name, help=description)
        parser.add_argument("case", type=Path)
        return parser

    command("init", "Create an empty case").add_argument("request")
    source = command("source", "Register or refresh a case source")
    source.add_argument("id")
    source.add_argument("kind", choices=SOURCE_KINDS)
    source.add_argument("file")
    source.add_argument("--url")
    source.add_argument(
        "--updates",
        nargs="*",
        default=[],
        help="IDs of previously reviewed sources or findings updated/challenged by this new source",
    )

    fetch_cmd = command("fetch", "Fetch an open HTTPS webpage snapshot and register it as a web source")
    fetch_cmd.add_argument("id")
    fetch_cmd.add_argument("url")
    fetch_cmd.add_argument("--out", help="Relative destination inside the case directory")
    fetch_cmd.add_argument("--timeout", type=float, default=12.0)
    fetch_cmd.add_argument(
        "--updates",
        nargs="*",
        default=[],
        help="IDs of previously reviewed sources or findings updated/challenged by this fetched source",
    )

    retire = command("retire", "Withdraw a source and preserve its history")
    retire.add_argument("id")
    retire.add_argument("--reason", required=True)

    command("evidence", "Read an archived snapshot by SHA-256").add_argument("sha")

    propose = command("propose", "Check and stage a recommendation (.json or .yaml/.yml template)")
    propose.add_argument("file")
    propose.add_argument("--strict-claims", action="store_true")

    draft_cmd = command("draft", "Generate a concise YAML draft template or compile a YAML draft to JSON")
    draft_cmd.add_argument("--template", action="store_true", help="Emit pre-populated YAML draft template")
    draft_cmd.add_argument("--compile", type=Path, help="Compile a YAML draft file into canonical proposal JSON")
    draft_cmd.add_argument("-o", "--output", type=Path, help="Write generated template or compiled JSON to file")

    accept = command("accept", "Record explicit approval of the drafted option")
    accept.add_argument("option")
    accept.add_argument("--expect", required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--reason", required=True)
    accept.add_argument("--allow-claim-warnings", action="store_true", help="Explicitly override pre-accept claim warnings")

    status = command("status", "Show what to resume, review, and follow up")
    status.add_argument("--today", type=date.fromisoformat, default=date.today())

    view = command("view", "Render a decision or draft as Markdown")
    choice = view.add_mutually_exclusive_group()
    choice.add_argument("--decision", type=int)
    choice.add_argument("--draft", action="store_true")

    checkpoint = command("checkpoint", "Save the stage and next step")
    checkpoint.add_argument("stage", choices=["intake", "research", "review", "follow-up"])
    checkpoint.add_argument("next")

    done = command("done", "Complete a follow-up task")
    done.add_argument("id")
    done.add_argument("--decision", type=int, required=True)
    done.add_argument("--note", required=True)

    route = command("route", "Choose the lowest-cost sufficient next workflow step and fallback")
    route.add_argument("--budget", choices=["lean", "balanced", "thorough"], default="balanced")
    route.add_argument("--failed-tool")
    route.add_argument("--error-reason")

    telemetry = command("telemetry", "Record or inspect run time, tokens, retries, and human gates")
    telemetry.add_argument("--step")
    telemetry.add_argument("--session", default="session-1")
    telemetry.add_argument("--model", default="zero_llm")
    telemetry.add_argument("--seconds", type=float, default=0.0)
    telemetry.add_argument("--input-tokens", type=int, default=0)
    telemetry.add_argument("--cached-tokens", type=int, default=0)
    telemetry.add_argument("--output-tokens", type=int, default=0)
    telemetry.add_argument("--tool-calls", type=int, default=1)
    telemetry.add_argument("--retries", type=int, default=0)
    telemetry.add_argument("--human-minutes", type=float, default=None)
    telemetry.add_argument("--estimated", action="store_true")
    telemetry.add_argument("--method", default="")
    telemetry.add_argument("--human", default="")
    telemetry.add_argument("--note", default="")

    cost_cmd = command("cost", "Import verified CLI runtime logs (--from-log), append a step, or summarize cost.jsonl")
    cost_cmd.add_argument("--from-log", nargs="+", type=Path, help="Raw CLI runtime log file(s) or run directory to import into cost.jsonl")
    cost_cmd.add_argument("--turns", type=Path, help="Optional turns.jsonl index with wall-clock timings and exit codes")
    cost_cmd.add_argument("--append", action="store_true", help="Append imported log steps instead of rebuilding cost.jsonl")
    cost_cmd.add_argument("--include-baseline", action="store_true", help="Include baseline_* entries when importing measured-telemetry.json")
    cost_cmd.add_argument("--step")
    cost_cmd.add_argument("--session", default="session-1")
    cost_cmd.add_argument("--model", default="zero_llm")
    cost_cmd.add_argument("--seconds", type=float, default=0.0)
    cost_cmd.add_argument("--input-tokens", type=int, default=0)
    cost_cmd.add_argument("--cached-tokens", type=int, default=0)
    cost_cmd.add_argument("--output-tokens", type=int, default=0)
    cost_cmd.add_argument("--tool-calls", type=int, default=1)
    cost_cmd.add_argument("--retries", type=int, default=0)
    cost_cmd.add_argument("--human-minutes", type=float, default=None)
    cost_cmd.add_argument("--estimated", action="store_true", help="Mark cost/token figures as estimated")
    cost_cmd.add_argument("--method", default="", help="Measurement or estimation methodology note")
    cost_cmd.add_argument("--human", default="")
    cost_cmd.add_argument("--note", default="")

    command("audit-claims", "Audit communication promises against product bounds and evidence kinds")

    dash = command("dashboard", "Generate or serve a self-contained interactive HTML decision dashboard")
    dash.add_argument("-o", "--output", type=Path)
    dash.add_argument("--serve", action="store_true", help="Serve dashboard on 127.0.0.1 with 1-click accept")
    dash.add_argument("--port", type=int, default=8765, help="Loopback port for --serve (default: 8765)")

    exp_strat = command(
        "export-strategies",
        "Export Product and Communication strategies (Markdown + PDF) directly from case.sqlite3",
    )
    exp_strat.add_argument("-o", "--out-dir", type=Path, help="Output directory (defaults to <case>/strategies)")
    exp_choice = exp_strat.add_mutually_exclusive_group()
    exp_choice.add_argument("--decision", type=int, help="Decision revision number (defaults to latest approved)")
    exp_choice.add_argument("--draft", action="store_true", help="Export staged draft instead of approved decision")
    exp_strat.add_argument("--prefix", default="strategy", help="Filename prefix (default: strategy -> strategy-product.md/.pdf)")
    exp_strat.add_argument("--no-pdf", action="store_true", help="Write Markdown files only without rendering PDFs")

    rates = sub.add_parser("rates", help="Compute auditable weighted rates and Wilson CIs from CSV/TSV/XLSX")
    rates.add_argument("file", type=Path)
    rates.add_argument("--group", required=True)
    rates.add_argument("--segment")
    rates.add_argument("--denominator", required=True)
    rates.add_argument("--numerators", nargs="+", required=True)
    rates.add_argument("--ci", action="store_true")
    rates.add_argument("--guardrail", dest="guardrails", action="append")
    rates.add_argument("--sheet", help="Worksheet name or 0-based index when reading .xlsx")

    xlsx_cmd = sub.add_parser("xlsx", help="Inspect worksheets and records of an OpenXML (.xlsx) workbook")
    xlsx_cmd.add_argument("file", type=Path)
    xlsx_cmd.add_argument("--sheet", help="Worksheet name or 0-based index")
    xlsx_cmd.add_argument("--format", choices=["json", "md"], default="json")

    econ = sub.add_parser("economics", help="Compute unit economics, cost headroom, and stress bounds")
    econ.add_argument("file", type=Path)
    econ.add_argument(
        "--format", choices=["json", "md"], default="json",
        help="md: таблица «факты vs допущения» и пороги смены решения для стратегии/паспорта",
    )

    kb = sub.add_parser("kb", help="Manage and search the cross-session knowledge base")
    kb.add_argument("--kb-dir", type=Path, default=Path(".kb"))
    kb_sub = kb.add_subparsers(dest="kb_action", required=True)

    kb_upsert = kb_sub.add_parser("upsert", help="Add or update a knowledge base entry")
    kb_upsert.add_argument("id")
    kb_upsert.add_argument("origin_class", choices=["primary_evidence", "synthetic_probe", "prior_conclusion"])
    kb_upsert.add_argument("--title", required=True)
    kb_upsert.add_argument("--body", required=True)
    kb_upsert.add_argument("--case", default="")
    kb_upsert.add_argument("--sha", default="")
    kb_upsert.add_argument("--locator", default="")
    kb_upsert.add_argument("--tags", nargs="*", default=[])

    kb_search = kb_sub.add_parser("search", help="Search knowledge base with epistemic guardrails")
    kb_search.add_argument("query", nargs="?", default="")
    kb_search.add_argument("--tag")
    kb_search.add_argument("--origin-class", choices=["primary_evidence", "synthetic_probe", "prior_conclusion"])
    kb_search.add_argument("--include-inactive", action="store_true")
    kb_search.add_argument("--limit", type=int, default=10)

    kb_link = kb_sub.add_parser("link", help="Link two knowledge base entries")
    kb_link.add_argument("from_id")
    kb_link.add_argument("relation", choices=["supports", "contradicts", "refines", "supersedes"])
    kb_link.add_argument("to_id")
    kb_link.add_argument("--note", default="")

    kb_retract = kb_sub.add_parser("retract", help="Retract or supersede an outdated KB entry")
    kb_retract.add_argument("id")
    kb_retract.add_argument("--reason", required=True)
    kb_retract.add_argument("--superseded-by", default="")

    kb_ingest = kb_sub.add_parser("ingest", help="Ingest a case's active sources and decisions into KB")
    kb_ingest.add_argument("case", type=Path)

    sub.add_parser("mcp", help="Run Model Context Protocol (MCP) server over stdio")

    audit_repo = sub.add_parser("audit-repo", help="Check repository for secret leaks, stray DBs, and invalid JSON")
    audit_repo.add_argument("root", nargs="?", type=Path, default=Path("."))
    audit_repo.add_argument("--history", action="store_true", help="Also scan all blobs across git commit history")

    return root.parse_args()


def run_kb(args):
    kb_dir = args.kb_dir.resolve()
    act = args.kb_action
    if act == "upsert":
        return upsert_entry(
            kb_dir,
            entry_id=args.id,
            title=args.title,
            body=args.body,
            origin_class=args.origin_class,
            case_name=args.case,
            source_sha=args.sha,
            locator=args.locator,
            tags=args.tags,
        )
    if act == "search":
        return search_kb(
            kb_dir,
            args.query,
            tag=args.tag,
            origin_class=args.origin_class,
            include_inactive=args.include_inactive,
            limit=args.limit,
        )
    if act == "link":
        return link_entries(
            kb_dir, from_id=args.from_id, to_id=args.to_id, relation=args.relation, note=args.note
        )
    if act == "retract":
        return retract_entry(
            kb_dir, entry_id=args.id, reason=args.reason, superseded_by=args.superseded_by
        )
    if act == "ingest":
        case = args.case.resolve()
        with load(case, write=False) as (state, _):
            return ingest_case_into_kb(kb_dir, case, state)
    raise ValueError(f"Unknown kb action: {act}")


def run_case(args):
    case = args.case.resolve()
    cmd = args.command
    if cmd == "init":
        init(case, args.request)
        return {"case": str(case), "stage": "intake"}

    if cmd == "cost" and getattr(args, "from_log", None):
        return import_cost_from_logs(
            case,
            list(args.from_log),
            turns_path=args.turns,
            append=args.append,
            include_baseline=args.include_baseline,
        )
    if cmd == "cost" and not args.step and not (case / "case.sqlite3").is_file() and (case / "cost.jsonl").is_file():
        return summarize_case_cost(case)

    write_cmds = {"source", "fetch", "retire", "propose", "accept", "checkpoint", "done"}
    if cmd in {"telemetry", "cost"} and args.step:
        write_cmds.add(cmd)

    fetched_meta = None
    if cmd == "fetch":
        fetched_meta = fetch_open_webpage(case, args.id, args.url, relative_out=args.out, timeout=args.timeout)

    with load(case, write=cmd in write_cmds) as (state, db):
        if cmd == "source":
            return register(args, case, state, db)
        if cmd == "fetch":
            shim = argparse.Namespace(
                id=args.id,
                kind="web",
                file=fetched_meta["relative_path"],
                url=fetched_meta["url"],
                updates=args.updates,
            )
            reg = register(shim, case, state, db)
            return {**reg, **fetched_meta}
        if cmd == "retire":
            record = state["sources"].get(args.id)
            if not record or record.get("retired") or not args.reason.strip():
                raise ValueError("An active source and a reason are required")
            record["retired"] = {"reason": args.reason, "at": now()}
            return {"id": args.id, "retired": record["retired"]}
        if cmd == "evidence":
            row = db.execute("SELECT content FROM evidence WHERE sha = ?", (args.sha,)).fetchone()
            if not row:
                raise ValueError("Unknown evidence snapshot")
            return row[0]
        if cmd == "checkpoint":
            if not args.next.strip():
                raise ValueError("Next step is empty")
            state["checkpoint"] = {"stage": args.stage, "next": args.next, "at": now()}
            return state["checkpoint"]
        if cmd in {"telemetry", "cost"}:
            if not args.step:
                if cmd == "cost":
                    return summarize_case_cost(case, state.get("telemetry", []))
                return summarize_telemetry(state.get("telemetry", []))
            tel = record_telemetry(
                state,
                step=args.step,
                model=args.model,
                seconds=args.seconds,
                input_tokens=args.input_tokens,
                cached_tokens=args.cached_tokens,
                output_tokens=args.output_tokens,
                tool_calls=args.tool_calls,
                retries=args.retries,
                human_intervention=args.human,
                note=args.note,
            )
            cost_rec = append_cost_entry(
                case,
                step=args.step,
                session=args.session,
                model=args.model,
                input_tokens=args.input_tokens,
                cached_tokens=args.cached_tokens,
                output_tokens=args.output_tokens,
                wall_seconds=args.seconds,
                tool_calls=args.tool_calls,
                retries=args.retries,
                human_minutes=args.human_minutes,
                human_intervention=args.human,
                estimated=args.estimated,
                method=args.method,
                note=args.note,
            )
            return cost_rec if cmd == "cost" else tel

        decisions = state["decisions"]
        draft = state["draft"]
        if cmd == "view":
            cs = summarize_case_cost(case, state.get("telemetry", []))
            if args.draft or (args.decision is None and not decisions):
                if not draft:
                    raise ValueError("No draft")
                return render(draft, state["sources"], cost_summary=cs)
            revision = args.decision if args.decision is not None else len(decisions)
            if not 1 <= revision <= len(decisions):
                raise ValueError("Decision revision does not exist")
            entry = decisions[revision - 1]
            return render(entry["proposal"], state["sources"], entry, cost_summary=cs)
        if cmd == "done":
            if not args.note.strip() or not decisions:
                raise ValueError("A recorded decision and completion note are required")
            current = decisions[-1]
            if current["revision"] != args.decision:
                raise ValueError("Follow-up belongs to a different decision revision")
            completions = current.setdefault("completions", {})
            for action in current["proposal"]["actions"]:
                if action["id"] == args.id and action["id"] not in completions:
                    completion = {"at": now(), "note": args.note}
                    completions[action["id"]] = completion
                    return {**action, "completion": completion}
            raise ValueError("No open follow-up with this ID")

        sources = source_health(case, state["sources"])
        if cmd == "status":
            return decision_status(state, sources, args.today.isoformat(), case)
        if cmd == "route":
            return route_next_step(
                case,
                state,
                sources,
                budget=args.budget,
                failed_tool=args.failed_tool,
                error_reason=args.error_reason,
            )
        if cmd == "audit-claims":
            target = draft or (decisions[-1]["proposal"] if decisions else None)
            if not target:
                raise ValueError("Case has neither draft nor approved decision to audit")
            return audit_claims(target, sources)
        if cmd == "dashboard":
            if getattr(args, "serve", False):
                pass
            else:
                out = write_dashboard(case, state, sources, args.output, db=db)
                return {"dashboard": str(out)}
        if cmd == "draft":
            if args.compile is not None:
                src_p = args.compile if args.compile.is_absolute() else case_file(case, str(args.compile))
                compiled = compile_draft_template(src_p.read_text(encoding="utf-8-sig"), sources)
                validate(compiled, sources)
                if args.output:
                    out_p = args.output.resolve()
                    out_p.parent.mkdir(parents=True, exist_ok=True)
                    out_p.write_text(dumps(compiled) + "\n", encoding="utf-8")
                    return {"compiled": str(out_p), "findings": len(compiled["findings"]), "options": len(compiled["options"])}
                return compiled
            tpl_text = generate_draft_template(case, state, sources)
            if args.output:
                out_p = args.output if args.output.is_absolute() else (case / args.output)
                out_p = out_p.resolve()
                out_p.parent.mkdir(parents=True, exist_ok=True)
                out_p.write_text(tpl_text, encoding="utf-8")
                return {"template": str(out_p), "active_sources": len([s for s in sources.values() if s["health"] != "retired"])}
            return tpl_text
        if cmd == "export-strategies":
            return export_strategies(
                case,
                state,
                sources,
                db,
                out_dir=args.out_dir,
                decision_rev=args.decision,
                use_draft=args.draft,
                prefix=args.prefix,
                include_pdf=not args.no_pdf,
            )
        if cmd == "propose":
            path = case_file(case, args.file)
            _, content = digest(path)
            raw_text = content.decode("utf-8-sig")
            if path.suffix.lower() in {".yaml", ".yml"}:
                proposal = compile_draft_template(raw_text, sources)
            else:
                proposal = json.loads(raw_text)
            validate(proposal, sources)
            claims = audit_claims(proposal, sources)
            if args.strict_claims and not claims["passed"]:
                messages = "; ".join(w["message"] for w in claims["warnings"])
                raise ValueError(f"Claim alignment check failed: {messages}")
            state["draft"] = proposal
            sequence = state["draft_sequence"] = state.get("draft_sequence", 0) + 1
            return {
                "draft": proposal["recommended"],
                "findings": len(proposal["findings"]),
                "options": len(proposal["options"]),
                "sha": fingerprint(proposal, sequence),
                "claim_warnings": claims["total_warnings"],
            }

        if cmd != "dashboard":
            return _apply_accept_in_state(
                state,
                sources,
                option=args.option,
                expect=args.expect,
                by=args.by,
                reason=args.reason,
                allow_claim_warnings=args.allow_claim_warnings,
            )

    # cmd == "dashboard" with --serve (runs outside read transaction)
    def _live_accept(target_case: Path, *, option: str, expect: str, by: str, reason: str) -> dict:
        with load(target_case, write=True) as (st, _):
            srcs = source_health(target_case, st["sources"])
            return _apply_accept_in_state(st, srcs, option=option, expect=expect, by=by, reason=reason)

    serve_dashboard(
        case,
        load_fn=load,
        source_health_fn=source_health,
        accept_fn=_live_accept,
        port=args.port,
    )
    return {"served": str(case)}


def _apply_accept_in_state(
    state: dict,
    sources: dict,
    *,
    option: str,
    expect: str,
    by: str,
    reason: str,
    allow_claim_warnings: bool = False,
) -> dict:
    decisions = state["decisions"]
    draft = state["draft"]
    if not draft or draft["recommended"] != option:
        raise ValueError("Option must match the current draft")
    if expect != fingerprint(draft, state.get("draft_sequence", 0)):
        raise ValueError("Draft differs from the recommendation that was reviewed")
    if outstanding(draft, sources):
        raise ValueError("Evidence changed. Review the case status before accepting.")
    pre_accept_claims = audit_claims(draft, sources)
    if not pre_accept_claims["passed"] and not allow_claim_warnings:
        msgs = "; ".join(w["message"] for w in pre_accept_claims["warnings"] if w["severity"] == "high")
        raise ValueError(f"Pre-accept hook blocked approval due to claim warnings: {msgs}")
    if not by.strip() or not reason.strip():
        raise ValueError("Approver and reason are required")
    decision = {
        "revision": len(decisions) + 1,
        "option": option,
        "by": by,
        "reason": reason,
        "at": now(),
        "sha": expect,
        "proposal": draft,
    }
    decisions.append(decision)
    state["draft"] = None
    if state.get("checkpoint", {}).get("stage") == "review":
        state["checkpoint"] = {
            "stage": "follow-up",
            "next": f"Сопровождение Решения №{decision['revision']} ({option}) и контроль триггеров пересмотра",
            "at": decision["at"],
        }
    return {k: v for k, v in decision.items() if k != "proposal"}


def main():
    args = parse_args()
    if args.command == "mcp":
        return serve_mcp(source_health_fn=source_health, decision_status_fn=decision_status)

    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    try:
        if args.command == "rates":
            result = summarize_rates(
                args.file,
                group=args.group,
                segment=args.segment,
                denominator=args.denominator,
                numerators=args.numerators,
                ci=args.ci,
                guardrails=args.guardrails,
                sheet=args.sheet,
            )
        elif args.command == "xlsx":
            result = read_xlsx_records(args.file, sheet=args.sheet)
            if args.format == "md":
                result = render_xlsx_markdown(result)
        elif args.command == "economics":
            result = evaluate_economics_file(args.file)
            if args.format == "md":
                result = render_assumptions_markdown(result)
        elif args.command == "kb":
            result = run_kb(args)
        elif args.command == "audit-repo":
            result = audit_repository(args.root, scan_history=args.history)
            if not result["ok"]:
                print(dumps(result), file=sys.stderr)
                return 2
        else:
            result = run_case(args)
        if isinstance(result, bytes):
            sys.stdout.buffer.write(result)
        else:
            print(result if isinstance(result, str) else dumps(result))
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"ledger: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
