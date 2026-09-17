# Contributing

Thanks for helping with Context Lab. Keep changes small and focused.

## Setup

```bash
git clone https://github.com/erreep/context-lab
cd context-lab
python3 -m pip install -e .
```

Or install the CLI with `pipx` / `uv tool` as in [README.md](README.md).

## Checks

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same suite on Python 3.11 and 3.12 for every pull request.

## Pull requests

1. Branch from current `main`.
2. Prefer the smallest useful diff; reuse existing helpers.
3. Open a PR against `main`. Do not push directly to `main` once branch protection is enabled.
4. Fill in a short summary of what changed and how you tested it.

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
