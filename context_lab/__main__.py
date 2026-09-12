import argparse
import json
import sys
from pathlib import Path

from .engine import ROOT, STRATEGIES, compile_context
from .evaluate import evaluate, markdown_report
from .provider import ModelEndpoint
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Context Lab: inspect and test task-targeted agent memory")
    parser.add_argument("--db", default=str(ROOT / "workspace" / "memory.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Initialize the synthetic demo corpus without replacing existing memories")
    setup = sub.add_parser("initiate", help="Check or initialize a project/ticket knowledge base once")
    setup.add_argument("--project", required=True)
    setup.add_argument("--ticket", default="")
    setup.add_argument("--path", help="Local Markdown/text folder, including an Obsidian folder")
    setup.add_argument("--empty", action="store_true", help="Initialize without existing notes")
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
    bench.add_argument("--suite", default=str(ROOT / "data/scenarios.json"))
    bench.add_argument("--budget", type=int, default=1200)
    bench.add_argument("--out", default=str(ROOT / "results/benchmark.json"))
    bench.add_argument("--embeddings", action="store_true")
    bench.add_argument("--model-planner", action="store_true")
    imp = sub.add_parser("import", help="Import JSON sources and/or structured memories")
    imp.add_argument("file")
    exp = sub.add_parser("export", help="Export sources, current memories, revision history and feedback")
    exp.add_argument("--out", required=True)
    draft = sub.add_parser("draft", help="Draft conditional lessons from one source through a configured model; does not save")
    draft.add_argument("--source", required=True)
    extract = sub.add_parser("mem0-extract", help="Extract reviewable candidate facts from one saved source using local Mem0/Ollama")
    extract.add_argument("--source", required=True)
    extract.add_argument("--project", required=True)
    extract.add_argument("--ticket", default="")
    sub.add_parser("mcp", help="Start the stdio MCP server")
    args = parser.parse_args()
    store = Store(args.db)
    try:
        if args.command == "demo":
            print(json.dumps(store.seed(ROOT / "data/memories.json")))
        elif args.command == "initiate":
            from .knowledge import initiate
            print(json.dumps(initiate(store, args.project, args.ticket, args.path, args.empty, args.refresh), indent=2))
        elif args.command == "serve":
            from .server import serve
            serve(args.db, port=args.port)
        elif args.command == "mcp":
            from .mcp import serve_mcp
            serve_mcp(store)
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
        elif args.command == "mem0-extract":
            from .mem0_bridge import extract
            print(json.dumps(extract(store, args.source, args.project, args.ticket), indent=2))
        else:
            adapter = ModelEndpoint(store) if args.embeddings or args.model_planner else None
            options = {"embeddings": adapter if args.embeddings else None, "planner": adapter if args.model_planner else None}
            if args.command == "context":
                task = json.loads(Path(args.task).read_text())
                packet = compile_context(store, task, args.strategy, args.budget, **options)
                print(packet["context"] if args.text else json.dumps(packet, indent=2))
            elif args.command == "benchmark":
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
