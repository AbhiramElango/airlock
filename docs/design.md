# Design

## What a run does

1. Checks the repo is a git top-level with a **clean working tree** (the checkpoint).
2. Reads every allowed file (within ~12k characters); a file named in the request, and its tests, go first.
3. A short model call routes the message: **question** or **fix** (unclear → question, so nothing changes).
4. A question gets a plain-English answer (max 3 sentences). For a fix, the tests run once and the failing
   output goes into the prompt.
5. The model replies with one plain-English sentence and **SEARCH/REPLACE blocks** for up to 3 files. It is told
   to fix only what was asked and leave unrelated bugs alone. A reply with one full file is accepted as a fallback.
6. The agent applies the blocks to its copies, builds a unified diff, and checks it: only the named non-test files,
   inside the repo (paths as git parses them), compiles, and `git apply --check`. Bad replies are retried with
   the error.
7. **Self-check:** the diff is applied to a scratch copy and the tests run there. If the change does not fix
   failing tests without breaking passing ones, the model gets one more round with the new test output and the
   names of any tests it broke.
8. You approve (Approve button or `y`). Only then: `git apply`, tests again, one record in `audit/audit.jsonl`.

### Edit format

```
The tip is 15 times too big because `bill * tip_percent` uses 15 instead of 15%.
FILE: bill.py
<<<<<<< SEARCH
    tip = bill * tip_percent
=======
    tip = bill * (tip_percent / 100)
>>>>>>> REPLACE
```

SEARCH must match exactly one place (trailing spaces and a uniform indentation shift are tolerated). Changed
lines only, instead of rewriting whole files, is what made multi-file fixes and larger files work.

## Web UI

Files, code view with ▶ Run, an **Output | Terminal** tab, and the agent chat. The header shows the network badge
(**● OFFLINE** from a live probe), free RAM, and a **model dropdown** listing installed Ollama models (switching
unloads the old model first). Proposed changes are shown **one card per file** with line numbers.

The Terminal tab is not a shell: only `ping`, `curl`, `nslookup`/`dig`, `ip addr|route`, `python <repo file>`,
`pytest`, `ls`, `cat <repo file>`, `clear` and `help`, each validated and mapped to a fixed argv (no shell, 10 s
timeout). The server uses only the standard library, accepts JSON-only POSTs, checks the Host header and loads
nothing external (CSP `default-src 'self'`).

## Modules

| Module | Responsibility |
| --- | --- |
| `cli.py` | Arguments, run flow, diff display, approval prompt, exit codes |
| `session.py` | Propose → self-check → approve/reject → verify; shared by CLI and UI |
| `agent.py` | Context selection (pluggable), routing, prompts, retries with feedback |
| `edits.py` | Parse SEARCH/REPLACE blocks (or one full file) into file edits |
| `patcher.py` | Diff build/validation, clean-tree check, scratch copy, `git apply` |
| `verify.py` | Fixed pytest command (timeout, scrubbed env), failing-test comparison |
| `repo_tools.py` | List/read/search confined to the root; skips `.git`, venvs, secrets, binary and large files |
| `llm.py` | Ollama client; refuses any non-loopback URL |
| `audit.py` | Redacted JSON-lines audit record per run |
| `web/` | Web UI server, allowlisted terminal, static page |

Dependencies are injected (LLM, approver, test runner, audit log), so the whole flow is tested with fakes:
`.venv/bin/python -m unittest discover -s tests -t .` (77 tests, ~9 s).

## Safety controls

- **Approval:** nothing is written to the repo before you approve; the self-check runs on a throwaway copy.
- **File access:** paths are resolved (symlinks included) and must stay under the root; `..`, absolute paths,
  symlink escapes, `.git`, venvs and secret-like names (`.env`, `*.pem`, `*secret*`, ...) are rejected.
- **No shell:** the model only returns edits. Subprocesses are `git` and the fixed pytest command, as argument
  lists with timeouts; pytest gets a minimal environment, so env secrets are not passed through.
- **Patches:** at most 3 non-test files, re-validated before apply; stale patches are refused.
- **Network:** the model client accepts only loopback; the UI binds `127.0.0.1` (or the isolated container).
- **Audit:** secrets (API keys, tokens, bearer headers, private keys) and home paths are redacted; file contents
  are not logged.

## Tested setup

Intel Core i5-6500T (4 cores, CPU only), 7.1 GB RAM, Ubuntu 26.04; Ollama 0.40.6, Python 3.14 (3.12 in the
container), pytest 9.1.1, git 2.53, Docker 29.9 with Compose 5.6. Model `qwen3.5:4b`, temperature 0, `num_ctx`
8192, thinking off; ~5 tokens/s. A fix on the 9-file demo repo takes ~90–110 s, a question ~30 s.
