# Security Policy

## Supported versions

This project is experimental and has no published PyPI release yet. Security fixes land on `main` only.

## Reporting a vulnerability

**Do not** open a public GitHub issue for security reports.

Prefer GitHub private vulnerability reporting once the owner enables it:

1. Open https://github.com/erreep/context-lab/security/advisories/new  
2. Or ask the owner to enable **Settings → Code security → Private vulnerability reporting**

Until that is enabled, contact the repository owner ([@erreep](https://github.com/erreep)) privately and wait for acknowledgment before any public disclosure.

Include steps to reproduce, impact, and whether a fix is known.

## Threat model

See the **Local threat model** section in [README.md](README.md). Context Lab is local-first and same-OS-user by design; `serve` and MCP are not a multi-user security boundary.
