import argparse
import json
import sys
from pathlib import Path

from .engine import DATA_ROOT, DEFAULT_DB, STRATEGIES
from .evaluate import evaluate, markdown_report
from .provider import ModelEndpoint
from .schemas import AgentError
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Context Lab: inspect and test task-targeted agent memory")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Initialize the synthetic demo corpus without replacing existing memories")
    setup = sub.add_parser("initiate", help="Check or initialize a project/ticket knowledge base once")
    setup.add_argument("--project", required=True)
    setup.add_argument("--ticket", default="")
    setup.add_argument("--path", help="Local Markdown/text folder for ticket notes (not necessarily the vault root)")
    setup.add_argument("--empty", action="store_true", help="Initialize ticket notes without existing files")
    setup.add_argument("--vault", help="Obsidian vault root path (required on first project touch unless --no-vault)")
    setup.add_argument("--no-vault", action="store_true", help="Decline a lab vault (import a ticket folder to journal)")
    setup.add_argument("--refresh", action="store_true", help="Explicitly refresh an existing snapshot")
    web = sub.add_parser("serve", help="Run the local inspection UI")
    web.add_argument("--port", type=int, default=8765)
    context = sub.add_parser("context", help="Build a context packet from a task JSON file")
    context.add_argument("--task", required=True)
    context.add_argument("--strategy", choices=STRATEGIES, default="targeted")
    context.add_argument("--budget", type=int, default=1200)
    context.add_argument("--text", action="store_true", help="Print only the agent-ready context")
    context.add_argument("--embeddings", action="store_true")
    context.add_argument("--model-planner", action="store_true")
    bench = sub.add_parser("benchmark", help="Run a synthetic context-selection comparison")
    bench.add_argument("--suite", default=str(DATA_ROOT / "scenarios.json"))
    bench.add_argument("--budget", type=int, default=1200)
    bench.add_argument("--out", default=str(Path.cwd() / "results/benchmark.json"))
    bench.add_argument("--embeddings", action="store_true")
    bench.add_argument("--model-planner", action="store_true")
    imp = sub.add_parser("import", help="Import JSON sources and/or structured memories")
    imp.add_argument("file")
    exp = sub.add_parser("export", help="Export sources, current memories, revision history and feedback")
    exp.add_argument("--out", required=True)
    draft = sub.add_parser("draft", help="Draft conditional lessons from one source through a configured model; does not save")
    draft.add_argument("--source", required=True)
    sub.add_parser("mcp", help="Start the stdio MCP server")
    usage_p = sub.add_parser("usage", help="Report local estimated token traffic (not billed usage)")
    usage_p.add_argument("--project", help="Filter to a project; includes all its tickets unless --ticket is set")
    usage_p.add_argument("--ticket", help="Filter to an exact ticket; use an empty string for project baseline")
    usage_p.add_argument("--json", action="store_true", help="Print structured counters")
    sub.add_parser("allocate-ticket", help="Allocate a generated work-unit ticket id (work-YYYYMMDD-HHMMSS UTC)")
    journal_p = sub.add_parser("journal", help="Write durable ticket journal evidence and index it")
    journal_p.add_argument("--project", required=True)
    journal_p.add_argument("--ticket", required=True)
    journal_p.add_argument("--kind", required=True, choices=["plan", "decision", "progress", "handoff"])
    journal_p.add_argument("--title", required=True)
    journal_p.add_argument("--body", required=True)
    from .hooks import build_parser as build_hook_parser, dispatch as dispatch_hook
    from .scope import build_parser as build_scope_parser, dispatch as dispatch_scope
    build_hook_parser(sub)
    build_scope_parser(sub)
    inst = sub.add_parser("install", help="One-shot local opt-in: MCP + client hooks + git lease (alias of hook install)")
    inst.add_argument("--client", required=True, choices=["claude", "codex", "cursor"])
    inst.add_argument("--project", default=None)
    inst.add_argument("--ticket", default=None)
    inst.add_argument("--db", default=None)
    inst.add_argument("--no-git", action="store_true", help="Skip pre-commit lease install")
    inst.add_argument("--force", action="store_true", help="Overwrite existing client files")
    inst.add_argument("--global", action="store_true", dest="global_install", help="User-global MCP only (no ambient hooks, no git lease)")
    args = parser.parse_args()
    if args.command == "scope":
        try:
            return dispatch_scope(args)
        except AgentError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
    if args.command == "hook":
        try:
            return dispatch_hook(args)
        except AgentError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
        except (ValueError, KeyError, OSError) as e:
            print("Error: " + str(e), file=sys.stderr)
            return 1
    if args.command == "install":
        # Lore-style top-level alias for hook install.
        from .hooks import install as install_client, install_global
        from .schemas import AgentError
        try:
            if args.global_install:
                if args.project is not None or args.ticket is not None or args.db is not None:
                    raise AgentError(
                        "validation",
                        "--global cannot be combined with --project/--ticket/--db",
                    )
                if args.no_git:
                    raise AgentError(
                        "validation",
                        "--global never installs a git lease; omit --no-git",
                    )
                return install_global(args.client, force=args.force)
            return install_client(
                args.client,
                project=args.project,
                ticket=args.ticket,
                db=args.db,
                git=not args.no_git,
                force=args.force,
            )
        except AgentError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
        except (ValueError, KeyError, OSError) as e:
            print("Error: " + str(e), file=sys.stderr)
            return 1
    store = Store(args.db)
    try:
        if args.command == "demo":
            print(json.dumps(store.seed(DATA_ROOT / "memories.json")))
        elif args.command == "initiate":
            from .agent_api import initiate
            if args.vault and args.no_vault:
                raise SystemExit("Use --vault or --no-vault, not both")
            knowledge = {}
            if args.no_vault:
                knowledge["vault"] = "none"
            elif args.vault:
                knowledge["vault"] = args.vault
            if args.empty:
                knowledge["mode"] = "empty"
            elif args.path:
                knowledge["mode"] = "import"
                knowledge["path"] = args.path
            elif knowledge.get("vault") and not args.ticket:
                pass  # vault-only baseline configure
            else:
                knowledge["mode"] = "auto"
            print(json.dumps(initiate(store, args.project, args.ticket, knowledge=knowledge or None,
                                      refresh=args.refresh), indent=2))
        elif args.command == "serve":
            from .server import serve
            serve(args.db, port=args.port)
        elif args.command == "mcp":
            from .mcp import serve_mcp
            serve_mcp(store)
        elif args.command == "usage":
            from .usage import report, format_report
            data = report(store, args.project, args.ticket)
            print(json.dumps(data, indent=2) if args.json else format_report(data))
        elif args.command == "allocate-ticket":
            from .knowledge import allocate_ticket
            print(json.dumps({"ticket": allocate_ticket()}, indent=2))
        elif args.command == "journal":
            from .agent_api import journal
            print(json.dumps(journal(store, args.project, args.ticket, args.kind, args.title, args.body), indent=2))
        elif args.command == "import":
            data = json.loads(Path(args.file).read_text())
            if data.get("knowledge_bases"):
                raise ValueError("Restore the SQLite backup to preserve knowledge-base setup; JSON import supports sources and memories only")
            sources = [store.add_source(s) for s in data.get("sources", [])]
            memories = store.put_memories(data["memories"]) if data.get("memories") else []
            print(json.dumps({"sources": len(sources), "memories": len(memories)}))
        elif args.command == "export":
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(store.export(), indent=2) + "\n")
            print(args.out)
        elif args.command == "draft":
            source = store.source(args.source)
            if not source:
                raise ValueError("Unknown source_id")
            print(json.dumps(ModelEndpoint(store).draft(source), indent=2))
        elif args.command == "context":
            from .agent_api import context as build_context
            task = json.loads(Path(args.task).read_text())
            view = build_context(store, task, budget=args.budget)
            print(view["context"] if args.text else json.dumps(view, indent=2))
        else:
            adapter = ModelEndpoint(store) if args.embeddings or args.model_planner else None
            options = {"embeddings": adapter if args.embeddings else None, "planner": adapter if args.model_planner else None}
            if args.command == "benchmark":
                report = evaluate(store, args.suite, args.budget, **options)
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, indent=2) + "\n")
                out.with_suffix(".md").write_text(markdown_report(report))
                print(markdown_report(report))
    except (ValueError, KeyError, OSError) as e:
        print("Error: " + str(e), file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
