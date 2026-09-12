"""Absolute-path launcher for MCP clients that do not support cwd."""
import sys
from context_lab.__main__ import main

sys.argv.append("mcp")
raise SystemExit(main())
