"""Synthetic context-selection evaluation. Not an end-to-end agent benchmark."""
import json
import statistics
from pathlib import Path

from .engine import STRATEGIES, compile_context, plan_task


def score_packet(packet, expected):
    selected = {m["id"] for m in packet["selected"]}
    required = set(expected.get("required", []))
    relevant = set(expected.get("relevant", [])) | required
    forbidden = set(expected.get("forbidden", []))
    missing = sorted(required - selected)
    harmful = sorted(forbidden & selected)
    irrelevant = sorted(selected - relevant)
    statuses = {n["need"]: n["status"] for n in packet["needs"]}
    wrong_gaps = {key: {"expected": value, "actual": statuses.get(key, "not_checked")}
                  for key, value in expected.get("gaps", {}).items() if statuses.get(key) != value}
    return {"required_recall": len(required & selected) / len(required) if required else None,
            "selection_precision": len(selected & relevant) / len(selected) if selected else (1.0 if not required else 0.0),
            "forbidden_count": len(harmful), "irrelevant_count": len(irrelevant),
            "context_case_pass": not missing and not harmful and not wrong_gaps,
            "missing_ids": missing, "forbidden_ids": harmful, "irrelevant_ids": irrelevant,
            "gap_mismatches": wrong_gaps, "estimated_tokens": packet["estimated_tokens"],
            "latency_ms": packet["latency_ms"]}


def evaluate(store, scenario_path, budget=1200, embeddings=None, planner=None):
    suite = json.loads(Path(scenario_path).read_text())
    cases = []
    for case in suite["cases"]:
        # Generate ONE task plan shared by all arms. Labels go only to score_packet.
        task = plan_task(case["task"], planner=planner)
        outcomes = {}
        for strategy in STRATEGIES:
            packet = compile_context(store, task, strategy, budget, embeddings=embeddings, persist=False, planning_metadata=task["planning"])
            outcomes[strategy] = {"metrics": score_packet(packet, case["expected"]), "packet": packet}
        cases.append({"id": case["id"], "label": case["label"], "category": case["category"],
                      "split": case.get("split", "illustrative"), "expected": case["expected"], "outcomes": outcomes})
    summary = {}
    for strategy in STRATEGIES:
        metrics = [c["outcomes"][strategy]["metrics"] for c in cases]
        recalls = [m["required_recall"] for m in metrics if m["required_recall"] is not None]
        latencies = sorted(m["latency_ms"] for m in metrics)
        summary[strategy] = {"cases": len(metrics), "context_cases_passed": sum(m["context_case_pass"] for m in metrics),
            "required_recall": statistics.mean(recalls) if recalls else None,
            "selection_precision": statistics.mean(m["selection_precision"] for m in metrics),
            "forbidden_inclusions": sum(m["forbidden_count"] for m in metrics),
            "mean_estimated_tokens": round(statistics.mean(m["estimated_tokens"] for m in metrics)),
            "p95_latency_ms": latencies[min(len(latencies)-1, int(len(latencies)*.95))]}
    return {"suite": suite["name"], "budget": budget, "summary": summary, "cases": cases,
            "limitations": ["Synthetic, authored fixtures; not independent evidence of superiority.",
                "Scores measure annotated context selection and declared evidence gaps, not agent task success.",
                "The default planner is a transparent regex catalog and can miss paraphrases or negation.",
                "Default retrieval is BM25; embeddings and a model planner are optional and require a configured endpoint.",
                "Lessons are hand-authored unless you draft and review your own. No autonomous learning is claimed.",
                "All arms share date, project, candidate and supersession filters, task features and budget.",
                "Coverage labels remain outside retrieval. Fixtures are development examples, not a sealed holdout.",
                "Budget uses estimated tokens. Latency excludes ingestion; embedding cache state affects timing."]}


def markdown_report(report):
    lines = ["# Context Lab: synthetic context-selection results", "", report["suite"], "",
             f"Context budget: {report['budget']} estimated tokens per arm.", "",
             "| Strategy | Context cases passed | Required recall | Selection precision | Forbidden inclusions | Mean estimated tokens |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, m in report["summary"].items():
        lines.append(f"| {name} | {m['context_cases_passed']}/{m['cases']} | {m['required_recall']:.1%} | {m['selection_precision']:.1%} | {m['forbidden_inclusions']} | {m['mean_estimated_tokens']} |")
    lines += ["", "## Interpretation limits", ""] + ["- " + x for x in report["limitations"]]
    lines += ["", "## Case-level failures", ""]
    for case in report["cases"]:
        for arm, result in case["outcomes"].items():
            m = result["metrics"]
            if not m["context_case_pass"]:
                lines.append(f"- **{case['id']} / {arm}:** missing={m['missing_ids']}; forbidden={m['forbidden_ids']}; gap mismatches={m['gap_mismatches']}")
    return "\n".join(lines) + "\n"
