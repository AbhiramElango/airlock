# Airlock

**An offline AI coding agent that fixes bugs in a sealed container — only with your approval.**

An offline coding assistant for small Python repos, powered by a **local Qwen model via Ollama**. Ask it in
plain English what is wrong; it reads the repo, explains the bug, proposes a minimal diff (across files if
needed), **tests the change on a scratch copy**, and applies it **only after you approve**. No hosted APIs,
no telemetry, no network calls at runtime. Use it from a CLI or a small IDE-style web UI.

```
You ─► route: question or fix? ─► read repo (scoped) ─► local model (127.0.0.1) ─► SEARCH/REPLACE edits
    ─► diff checks (git) ─► self-check: tests on a scratch copy ─► you approve ─► git apply ─► tests ─► audit log
```

## Prerequisites (one-time, while online)

- **Ollama** with the model pulled. This is a prerequisite; nothing downloads a model at runtime.
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh     # macOS: brew install ollama, or the app from ollama.com
  ollama pull qwen3.5:4b                            # 3.3 GB, Q4_K_M; ~5 GB RAM while loaded
  ```
- **Python 3.12+** and **git**. For the container mode also **Docker** with Compose (Linux).

## Run it on your machine (no Docker)

Linux or macOS (Windows: use WSL). Ollama runs as usual on `127.0.0.1:11434`.

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
./scripts/reset_demo.sh                                  # disposable demo repo at demo/work
bin/airlock-ui --repo demo/work --reset-script scripts/reset_demo.sh
# open http://localhost:8080
```

Or the CLI: `bin/airlock --repo demo/work "what is wrong with bill.py?"`.

**On your own code:** pass `--repo /path/to/repo`. It must be a git repo with a clean working tree and pytest
tests; small repos work best (~12k characters of code are read). Without `--reset-script` there is no Reset button.

This mode relies on the agent's own controls (loopback-only model client, no shell, approval). For network
isolation, use the container.

## Run it in an isolated container (Linux + Docker)

Agent and Ollama run together in one container on an internal Docker network with **no route out**. The model
files are mounted read-only from the host's Ollama folder (auto-detected; or set `MODELS_DIR`).

```bash
sudo ./scripts/container.sh up         # build, start, block container -> host, run the isolation checks
# open http://172.30.0.10:8080 on this machine
sudo ./scripts/container.sh down       # stop; resumes the host's Ollama if `up` paused it
```

Other commands: `restart` (after code changes), `proof` (re-run the checks), `agent "<task>"` (CLI inside),
`shell`. Options: `MODEL=<tag>` (default `qwen3.5:4b`), `MODELS_DIR=<path>`.
From another machine, tunnel over SSH: `TARGET=172.30.0.10 ./scripts/open_ui_mac.sh` (`HOST` defaults to `mini-pc`).

What `up` checks inside the container (everything blocked except the local model):

```
routes inside the container:  172.30.0.0/24 dev eth0   (no default route)
[ok] ping 1.1.1.1: blocked            [ok] HTTPS https://ollama.com: blocked
[ok] DNS lookup ollama.com: blocked   [ok] host 172.30.0.1:22: blocked
[ok] host LAN gateway: blocked        [ok] local Ollama on 127.0.0.1:11434: reachable
```

Container hardening: runs as your user (not root), all capabilities dropped, `no-new-privileges`, read-only root
filesystem, memory and process limits; only `demo/work` and `audit/` are writable. An nftables rule on the host
drops connections *from* the container to the host, while replies to the host (the UI) still flow.

## Fully air-gapped run (your own machine)

Browser, agent and model all run on one machine, so it can be physically disconnected.

1. **While online, once:** install Ollama and `ollama pull qwen3.5:4b`; create `.venv` with pytest; clone this repo.
   For the container mode, run `sudo ./scripts/container.sh up` once so the image is built (the build needs
   internet), then `down`.
2. **Disconnect:** unplug the network cable and turn off Wi-Fi/Bluetooth (or airplane mode).
3. **Run:** no Docker: `bin/airlock-ui --repo demo/work --reset-script scripts/reset_demo.sh` and open
   `http://localhost:8080`; Linux + Docker: `sudo ./scripts/container.sh up` and open `http://172.30.0.10:8080`.
4. **Check:** the badge shows **● OFFLINE**; `ping 1.1.1.1` and `curl https://google.com` fail in the Terminal tab;
   the fix flow still works.

On macOS/Windows use the no-Docker mode. The container mode plus a disconnected machine is the strongest setup:
no network at all, and the agent sandboxed on top.

## The demo repo

`demo/seed` is a small restaurant project (9 files) with two independent bugs:

| Ask | Symptom | Cause | Expected fix |
| --- | --- | --- | --- |
| "what is wrong with bill.py?" | `python bill.py`: each person pays **$320.00** | tip uses 15 instead of 15% | `bill.py` |
| "the receipt total is way too high" | `python receipt.py`: total **$166.50** | `tax.py` treats 8 (%) as a fraction | **`tax.py`** (a different file) |

After approving, ▶ Run shows $23.00 and $19.98. **↺ Reset demo** restores the seed.

## Web UI

Files, code view with ▶ Run, an **Output | Terminal** tab, and the agent chat. The header shows the network
badge (**● OFFLINE** from a live probe), free RAM, and a **model dropdown** listing installed Ollama models
(switching unloads the old model first). Proposed changes are shown **one card per file** with line numbers;
click a card to open the file.

The Terminal tab is not a shell: only `ping`, `curl`, `nslookup`/`dig`, `ip addr|route`, `python <repo file>`,
`pytest`, `ls`, `cat <repo file>`, `clear` and `help`, each validated and mapped to a fixed argv (no shell, 10 s
timeout). The server uses only the standard library, accepts JSON-only POSTs, checks the Host header and loads
nothing external (CSP `default-src 'self'`).

## What a run does

1. Checks the repo is a git top-level with a **clean working tree** (the checkpoint).
2. Reads every allowed file (within ~12k characters); a file named in the request, and its tests, go first.
3. A short model call routes the message: **question** or **fix** (unclear → question, so nothing changes).
4. A question gets a plain-English answer (max 3 sentences). For a fix, the tests run once and the failing
   output goes into the prompt.
5. The model replies with one plain-English sentence and **SEARCH/REPLACE blocks** for up to 3 files. It is told
   to fix only what was asked, even if it notices other bugs. A reply with one full file is accepted as a fallback.
6. The agent applies the blocks to its copies, builds a unified diff, and checks it: only the named non-test files,
   inside the repo (paths as git parses them), compiles, and `git apply --check`. Bad replies are retried with the error.
7. **Self-check:** the diff is applied to a scratch copy and the tests run there. If the change does not fix
   failing tests without breaking passing ones, the model gets one more round with the new test output.
8. You approve (Approve button or `y`). Only then: `git apply`, tests again, one record in `audit/audit.jsonl`.

## Evaluation

`evals/run_eval.py` runs the agent end to end (auto-approve) on 13 small repos with a known bug, each failing its
tests beforehand, and records: tests pass after the fix, the right files changed, time. Cases include a helper bug
whose symptom is in the caller, a misspelled import, swapped arguments, a renamed field needing changes in two
files, a 122-line module, a request that names the wrong file, and the demo repo.

```bash
.venv/bin/python evals/run_eval.py [--model TAG ...] [--case NAME ...]   # needs Ollama on the host
```

| Design (qwen3.5:4b) | Passed | Right files |
| --- | --- | --- |
| One file per fix, model rewrites the whole file | 7/10 (two-file bugs failed, 122-line file timed out) | 7/10 |
| SEARCH/REPLACE edits across files | 9/10 | 10/10 |
| + self-check on a scratch copy | 12/12 (avg 71 s) | 12/12 |
| + "fix only what was asked" (current) | **12/13** (avg 84 s) | 13/13 |

The last row added a case phrased like a real user ("I think there is something wrong in receipt.py file"):
before the scope rule the model also fixed an unrelated bug in `bill.py`; now the fix stays in `tax.py`. The
cost is `rename_field`: the model either renames the field back (the self-check catches the broken test) or
targets the right callers but miscopies the SEARCH lines. Results vary between runs even at temperature 0, so
these single runs show the design works on small repos; they are not a benchmark.

Model comparison on the first 6 cases: `qwen3.5:4b` 6/6, `qwen2.5-coder:3b` 2/6 (mostly misrouted fixes as
questions).

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

Dependencies are injected (LLM, approver, test runner, audit log), so the whole flow is tested with fakes.

## Safety controls

- **Approval:** nothing is written to the repo before you approve; the self-check runs on a throwaway copy.
- **File access:** paths are resolved (symlinks included) and must stay under the root; `..`, absolute paths,
  symlink escapes, `.git`, venvs and secret-like names (`.env`, `*.pem`, `*secret*`, ...) are rejected.
- **No shell:** the model only returns edits. Subprocesses are `git` and the fixed pytest command, as argument lists
  with timeouts; pytest gets a minimal environment, so env secrets are not passed through.
- **Patches:** at most 3 non-test files, re-validated before apply; stale patches are refused.
- **Network:** the model client accepts only loopback; the UI binds `127.0.0.1` (or the isolated container).
- **Audit:** secrets (API keys, tokens, bearer headers, private keys) and home paths are redacted; file contents
  are not logged.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .  # 76 tests, ~9 s
```

## Tested setup

Intel Core i5-6500T (4 cores, CPU only), 7.1 GB RAM, Ubuntu 26.04; Ollama 0.40.6, Python 3.14 (3.12 in the
container), pytest 9.1.1, git 2.53, Docker 29.9 with Compose 5.6. Model `qwen3.5:4b`, temperature 0,
`num_ctx` 8192, thinking off; ~5 tokens/s. A fix on the 9-file demo repo takes ~90–110 s, a question ~30 s.

## Other offline tests

- **Host firewall lockdown** (`scripts/lockdown_demo.sh`): nftables drops everything except loopback and the current
  SSH client (15 min failsafe), proves isolation, then opens an `[OFFLINE]` shell with `agent`, `ui` and
  `prove-offline`. Passed on 2026-10-20 with the CLI and web UI. Transcript: [`docs/`](docs/).
- **All interfaces down** (`scripts/offline_run.sh`, run via `systemd-run`): takes every interface down, proves it,
  runs the demo unattended, restores the network. Passed on 2026-10-20.

Wording: these setups are **network-isolated** (the machine stays connected, traffic is blocked) or **fully
offline** (interfaces down). Only with the cable unplugged would it be **air-gapped**.

## Known limitations

- Small Python repos with pytest; context capped at ~12k characters; up to 3 files per fix.
- A 4B model can still be wrong, and its one-sentence explanation is sometimes imprecise even when the diff is
  right; the diff review, self-check and tests are the safety net.
- The agent never edits test files, so it cannot "fix" a bug by weakening the tests.
- Requires a clean git tree; untracked files count as dirty.
- The container shares the host kernel (not a VM), and Docker itself runs as root.
- Mac/Windows Docker Desktop cannot reach the container's IP; use the no-Docker mode there.
- Redaction is regex-based and errs toward over-redacting.
- CPU-only inference is slow (~5 tokens/s).
