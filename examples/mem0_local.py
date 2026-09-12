"""Minimal Docker-free Mem0 setup using Ollama and embedded Qdrant."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "workspace" / "mem0"
DATA.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MEM0_DIR", str(DATA))
os.environ.setdefault("MEM0_TELEMETRY", "false")

from mem0 import Memory


def main():
    memory = Memory.from_config({
        "llm": {"provider": "ollama", "config": {
            "model": "qwen3:4b", "ollama_base_url": "http://127.0.0.1:11434",
            "temperature": 0.1, "max_tokens": 1200,
        }},
        "embedder": {"provider": "ollama", "config": {
            "model": "embeddinggemma:300m", "ollama_base_url": "http://127.0.0.1:11434",
            "embedding_dims": 768,
        }},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "context_lab_mem0", "path": str(DATA / "qdrant"),
            "embedding_model_dims": 768, "on_disk": True,
        }},
        "history_db_path": str(DATA / "history.sqlite3"),
    })
    memory.add("My preferred editor for Context Lab is Zed.", user_id="local-smoke-test")
    print(json.dumps(memory.search("Which editor do I prefer?", filters={"user_id": "local-smoke-test"}), indent=2))


if __name__ == "__main__":
    main()
