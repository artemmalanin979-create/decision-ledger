"""Local tools for an IDE product-research agent."""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .review import impact, render, validate
from .store import case_file, digest, dumps, init, load, now
from .table import summarize_rates


def source_health(case, records):
    health = {}
    for key, record in records.items():
        version = record["versions"][-1]
        current = {**version, "kind": record["kind"], "url": record["url"], "health": "ok"}
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
        if (url.scheme != "https" or not url.hostname or url.username
                or any(char.isspace() or char in "<>()" for char in args.url)):
            raise ValueError("Web snapshots require an HTTPS source URL without credentials")
    elif args.url:
        raise ValueError("--url is reserved for web snapshots")
    path = case_file(case, args.file)
    sha, content = digest(path)
    record = state["sources"].setdefault(args.id, {"kind": args.kind, "url": args.url, "versions": []})
    if (args.kind, args.url) != (record["kind"], record["url"]):
        raise ValueError("Use a new source ID when the type or URL changes")
    relative = Path(args.file).as_posix()
    record.pop("retired", None)
    versions = record["versions"]
    if not versions or (sha, relative) != (versions[-1]["sha"], versions[-1]["path"]):
        db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)", (sha, content))
        versions.append({"sha": sha, "path": relative, "at": now()})
    return {"id": args.id, **versions[-1]}


def outstanding(proposal, sources):
    return any(impact(proposal, sources).values())


def fingerprint(proposal, sequence):
    return hashlib.sha256(dumps([sequence, proposal]).encode("utf-8")).hexdigest()


def decision_status(state, sources, today):
    history = state["decisions"]
    approved = history[-1] if history else None
    current = approved["proposal"] if approved else None
    draft = state["draft"]
    actions = []
    if approved:
        for action in current["actions"]:
            completion = approved.get("completions", {}).get(action["id"])
            status = "done" if completion else ("overdue" if action["due"] < today else "open")
            actions.append({**action, "completion": completion, "status": status})
    return {
        "request": state["request"],
        "checkpoint": state["checkpoint"],
        "sources": sources,
        "draft": ({"title": draft["title"], "option": draft["recommended"], "sha": fingerprint(draft, state.get("draft_sequence", 0))} if draft else None),
        "draft_impact": impact(draft, sources) if draft else None,
        "approved": ({k: v for k, v in approved.items() if k not in {"proposal", "completions"}} |
                     {"actions": actions} if approved else None),
        "approved_impact": impact(current, sources) if current else None,
        "history": [{"revision": entry["revision"], "option": entry["option"], "at": entry["at"]}
                    for entry in history],
    }


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
    source.add_argument("kind", choices=["document", "table", "web", "synthetic"])
    source.add_argument("file")
    source.add_argument("--url")
    retire = command("retire", "Withdraw a source and preserve its history")
    retire.add_argument("id")
    retire.add_argument("--reason", required=True)
    command("evidence", "Read an archived text snapshot by SHA-256").add_argument("sha")
    command("propose", "Check and stage a recommendation").add_argument("file")
    accept = command("accept", "Record explicit approval of the drafted option")
    accept.add_argument("option")
    accept.add_argument("--expect", required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--reason", required=True)
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
    rates = sub.add_parser("rates", help="Compute auditable weighted rates from CSV")
    rates.add_argument("file", type=Path)
    rates.add_argument("--group", required=True)
    rates.add_argument("--segment")
    rates.add_argument("--denominator", required=True)
    rates.add_argument("--numerators", nargs="+", required=True)
    return root.parse_args()


def run_case(args):
    case = args.case.resolve()
    cmd = args.command
    if cmd == "init":
        init(case, args.request)
        return {"case": str(case), "stage": "intake"}
    with load(case, write=cmd in {"source", "retire", "propose", "accept", "checkpoint", "done"}) as (state, db):
        if cmd == "source":
            return register(args, case, state, db)
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
        decisions = state["decisions"]
        draft = state["draft"]
        if cmd == "view":
            if args.draft or (args.decision is None and not decisions):
                if not draft:
                    raise ValueError("No draft")
                return render(draft, state["sources"])
            revision = args.decision if args.decision is not None else len(decisions)
            if not 1 <= revision <= len(decisions):
                raise ValueError("Decision revision does not exist")
            entry = decisions[revision - 1]
            return render(entry["proposal"], state["sources"], entry)
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
            return decision_status(state, sources, args.today.isoformat())
        if cmd == "propose":
            path = case_file(case, args.file)
            _, content = digest(path)
            proposal = json.loads(content.decode("utf-8-sig"))
            validate(proposal, sources)
            state["draft"] = proposal
            sequence = state["draft_sequence"] = state.get("draft_sequence", 0) + 1
            return {"draft": proposal["recommended"], "findings": len(proposal["findings"]),
                    "options": len(proposal["options"]), "sha": fingerprint(proposal, sequence)}
        if not draft or draft["recommended"] != args.option:
            raise ValueError("Option must match the current draft")
        if args.expect != fingerprint(draft, state.get("draft_sequence", 0)):
            raise ValueError("Draft differs from the recommendation that was reviewed")
        if outstanding(draft, sources):
            raise ValueError("Evidence changed. Review the case status before accepting.")
        if not args.by.strip() or not args.reason.strip():
            raise ValueError("Approver and reason are required")
        decision = {"revision": len(decisions) + 1, "option": args.option,
                    "by": args.by, "reason": args.reason, "at": now(), "sha": args.expect, "proposal": draft}
        decisions.append(decision)
        state["draft"] = None
        return {k: v for k, v in decision.items() if k != "proposal"}


def main():
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        if args.command == "rates":
            result = summarize_rates(args.file, group=args.group, segment=args.segment,
                                     denominator=args.denominator, numerators=args.numerators)
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
