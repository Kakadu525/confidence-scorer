# confidence-scorer

[![Tests](https://github.com/Kakadu525/confidence-scorer/actions/workflows/tests.yml/badge.svg)](https://github.com/Kakadu525/confidence-scorer/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-Confidence%20Scorer-2088FF?logo=github)](https://github.com/marketplace/actions/confidence-scorer)

**English** | [Русский](README.ru.md)

A confidence score for AI-generated pull requests and diffs. Before a merge,
the diff goes through three independent checks and comes out with a 0-100
score and a verdict: safe to merge, needs review, or do not merge.

The main check does not ask an AI anything. It runs the **old and the new
version of every changed function on the same generated inputs** and reports
a reproducible counterexample when their behavior differs. Tests written by
the same AI that wrote the code can't give you that.

![confidence-score report: counterexample found, score 35/100](docs/images/run-bug.png)

<sub>A real run on the demo in <a href="example_demo/">example_demo/</a>: all three checks on a local qwen2.5-coder:7b model through Ollama.</sub>

## Contents

- [Why](#why)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Installation](#installation)
- [AI keys and models](#ai-keys-and-models)
- [CLI](#cli)
- [Configuration](#configuration-confidenceyml)
- [GitHub Action](#github-action)
- [Security](#security)
- [Limitations](#limitations)
- [Development](#development)

## Why

AI writes more and more pull requests. These diffs look tidy, pass the linter
and often pass the unit tests too. But the tests were written by the same AI
as the code, so they confirm that the AI wrote what it meant to write. They say
nothing about whether the code still behaves the way it used to. A human
reviewer can no longer read every such PR as carefully as before.
confidence-scorer takes over part of that work:

1. **Property-based differential testing.** For every changed function it
   generates random and deliberately edge-case inputs, calls the old and the
   new version on the same inputs and compares the results. If the behavior
   diverges, the report gets a reproducible counterexample.
2. **Semantic diff.** An AI describes what changed in the behavior of each
   function (formatting doesn't count) and rates the risk.
3. **Independent second reviewer.** Another AI looks at the whole diff without
   seeing the conclusions of the first two checks and gives its own confidence
   score with a list of findings.

The three numbers are folded into one score with configurable weights and
thresholds. A confirmed counterexample for a public function caps the final
score, whatever the AI reviewers say (the `hard_fail` section of the config).

## Quick start

```bash
pip install -e ".[all]"              # from the repo root; [all] = Anthropic and OpenAI SDKs
cd confidence_scorer/js_helpers && npm install && cd ../..   # needed for JS/TS checks

confidence-score init                # create confidence.yml (optional, defaults exist)
```

Set the keys. Both are optional, see
[AI keys and models](#ai-keys-and-models) for details.

```bash
# bash / zsh / Git Bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

```powershell
# PowerShell (Windows): lasts until the terminal window is closed
$env:ANTHROPIC_API_KEY = "..."
$env:OPENAI_API_KEY = "..."
```

Check that the keys were picked up. `doctor` sends nothing to any API and
costs nothing, it only shows which checks will run:

```bash
confidence-score doctor
confidence-score run --base main --head HEAD
```

![confidence-score doctor: all checks ready](docs/images/doctor.png)

[`example_demo/`](example_demo/) contains a demo: a real bug that the property
test catches, and a safe refactoring that passes the same test.

No API keys at all? Point every AI check at a local model through
[Ollama](#ollama-free-and-local) and the whole pipeline runs for free on your
machine.

## How it works

```
                      git diff (base...head)
                              │
             ┌────────────────┴─────────────────┐
             │   changed .py / .js / .ts files  │
             └────────────────┬─────────────────┘
                              │
         AST/Babel diff: which functions actually changed
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
 1. differential        2. semantic diff     3. second AI reviewer
 property testing       (AI, per function)   (AI, whole diff)
 (Hypothesis /                                fresh eyes, doesn't see
  fast-check,                                 the output of (1) and (2)
  no AI: types,
  or AI as a fallback
  for generators)
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                    scoring.py: weighted score
                    + hard-fail rules
                              │
                              ▼
               score 0-100 + verdict + report
          (terminal / markdown PR comment / JSON)
```

### 1. Differential property-based testing

Only changed functions are tested: added and removed ones have no second
version to compare against. For each of them confidence-scorer:

1. Builds an input generator. If the parameters have type hints
   (`int`, `str`, `list[int]`, `Optional[str]`, TS `number`/`string`/...),
   the generator is built directly, without AI. If there are no types, an AI
   can propose the generator (`providers.strategy_generation` in the config).
   Its answer is never run through `eval`/`exec`: it is parsed against a fixed
   allowlist of strategies (`confidence_scorer/checks/strategy_builder.py`),
   and the AI can't pass anything outside that list.
2. Generates about 50 inputs (configurable), biased towards edge values:
   0, ±1, ±2, ±3 and so on. A purely random search over the whole `int` range
   almost never finds these bugs.
3. Calls the old and the new version of the function on every input and
   compares both the result and whether an exception was raised.
4. Hypothesis or fast-check shrinks any divergence down to a minimal
   counterexample, which goes into the report.

Top-level functions in Python and JS/TS (through Node, Babel and fast-check)
are supported. Class methods are not, see [Limitations](#limitations).

Runs are deterministic: `hypothesis.seed` and `js.seed` are fixed by default,
so re-running CI gives the same PR the same verdict. With `seed: null` every
run searches for new inputs, but the result may change from run to run.

### 2. Semantic diff

Every changed function gets its own AI request with the old and the new
source. The model lists behavioral changes with a severity and gives an
overall risk score from 0 to 100. This check catches what random inputs have a
hard time finding: changes to the contract, error handling, side effects and
documented behavior.

### 3. Second AI reviewer

One AI request for the whole diff. The prompt tells the model outright that
the code may have been written by an AI, that it deserves skepticism and that
nobody has checked it yet. The answer is a confidence score from 0 to 100, a
one-sentence verdict and a list of findings.

By default `semantic_diff` and `second_reviewer` use different providers,
Anthropic and OpenAI, so that the second opinion doesn't repeat the blind
spots of the first model. Any other combination can be set in
`confidence.yml` (the `providers:` section).

### Folding into a score

```
overall = Σ(sub_score_i × weight_i) over the checks that were able to run
```

If a check didn't run (no AI key, no suitable changed functions), it is
excluded and its weight is split proportionally among the rest. If none ran,
the score is N/A, not 100.

Redistributing weights alone isn't enough. Without AI keys the property tests
might check one trivial function, and the run would report 100/100 "safe to
merge". So the result is also capped by coverage:

```
coverage = Σ(check weight × its completeness) / (sum of all weights)
cap      = evidence.min_cap + (100 - evidence.min_cap) × coverage
overall  = min(overall, cap)
```

Completeness of the property tests and of the semantic diff is 1 or 0 (ran or
didn't). For the reviewer panel it is the weighted share of members that
answered.

With the default settings a single property test (weight 0.40) gives a cap of
70, which means the verdict "review recommended". The report always shows the
coverage and the cap that was applied. The cap can be turned off with
`evidence.enabled: false`.

Technical errors don't count as failures. The `error` status (module not
installed, worker crashed, timeout) means "could not be checked": such
functions are left out of the sub-score denominator and listed in the notes.

A model's score can't contradict its own findings. In one live run the second
reviewer found a removed boundary check with severity `high` and in the same
answer gave a confidence of 100. So the second reviewer's `confidence` and the
per-function `risk_score` in the semantic diff are capped by the worst issue
the model found: no more than 40 for `high`, no more than 75 for `medium`. The
original value stays in the JSON report, and the notes explain the downgrade.

With `hard_fail.enabled: true` (the default) a confirmed counterexample for a
public function caps `overall` at
`hard_fail.cap_score_on_confirmed_counterexample` (35 by default).

## Installation

```bash
pip install -e ".[all]"            # recommended: package + Anthropic and OpenAI SDKs
pip install -e ".[anthropic]"      # Anthropic only (if you don't need an OpenAI key)
pip install -e .                   # no SDKs: AI checks won't run

cd confidence_scorer/js_helpers && npm install      # JS/TS checks (optional)
```

Provider SDKs are installed separately. If a key is set but its SDK is
missing, the check is skipped, and both the report and `confidence-score
doctor` say which package to install.

Python 3.10 or newer is required. JS/TS checks need Node.js 18+. Without it the
JS/TS property tests are skipped with a reason in the report, everything else
works.

## AI keys and models

### Which check uses which key

| Check | What it does | Calls per run | Default key |
|---|---|---|---|
| `semantic_diff` | describes behavioral changes of each function | one per changed function (up to 25) | `ANTHROPIC_API_KEY` |
| `second_reviewer` | independent review of the whole diff | 1 | `OPENAI_API_KEY` |
| `strategy_generation` | builds input generators for functions without type hints | one per such function | `ANTHROPIC_API_KEY` |
| property tests | run the old and the new version of the function | 0 | not needed |

By default the second reviewer uses a different provider than the semantic
diff: models from the same provider share blind spots.

### What happens with one key or none

A check without a key is skipped, the run doesn't fail. The score is then
capped by the share of checks that ran ([coverage cap](#folding-into-a-score)):

| Keys | What runs | Score cap |
|---|---|---|
| both | everything | 100 |
| Anthropic only, default config | everything except the second reviewer | 82 |
| Anthropic only, reviewer switched to `anthropic` | everything | 100 |
| none | property tests only | 70 |
| none, AI checks on local Ollama | everything | 100 |

The reviewer is switched to Anthropic in `confidence.yml`. A ready-made,
commented-out variant is in the `confidence-score init` template:

```yaml
providers:
  second_reviewer:
    provider: anthropic
    model: claude-opus-5
```

### Getting a key

A Claude Pro/Max or ChatGPT Plus subscription does not give API access: those
are separate products with separate billing. Without a funded API balance the
key will be created, but every request made with it will be rejected.

- Anthropic: [console.anthropic.com](https://console.anthropic.com) →
  Billing (add funds) → API Keys → Create Key.
- OpenAI: [platform.openai.com](https://platform.openai.com) →
  Billing → API keys → Create new secret key.

The key is shown only once, copy it right away. For a test key, set a spending
limit in the provider's console.

Keys are never written to `confidence.yml`. They belong in environment
variables (locally) or GitHub Secrets (for the Action).

### Models and cost

By default every Anthropic task runs on `claude-opus-5`. The model and the
thinking depth can be set per check:

| Model | Input / output, $ per 1M tokens | When to use |
|---|---|---|
| `claude-opus-5` | 5 / 25 | default; best review accuracy |
| `claude-sonnet-5` | 2 / 10 | cheaper; reasonable for `semantic_diff` and `strategy_generation` |
| `claude-haiku-4-5` | 1 / 5 | cheapest, for narrow tasks |

```yaml
providers:
  semantic_diff:
    provider: anthropic
    model: claude-opus-5
    effort: medium        # low | medium | high | xhigh | max; default is high
```

`effort` sets the thinking depth. `low` and `medium` are faster and cheaper,
and for a narrow task like the semantic diff they are often enough, but check
that on your own PRs. The parameter only applies to Anthropic.

A small PR costs cents, a large one (25 changed functions) comes to about a
dollar. Re-running the same PR is almost free thanks to the
[cache](#configuration-confidenceyml): only the changed functions are billed.

### Free and cheap models

Any of the three AI checks can be handed to another provider by changing
`provider` and `model` in `confidence.yml`:

| `provider` | Cost | Key | Where your code goes |
|---|---|---|---|
| `ollama` | free | not needed | nowhere, the model runs on your computer |
| `deepseek` | paid, but dozens of times cheaper than Opus | `DEEPSEEK_API_KEY` | DeepSeek |
| `qwen` | usually has a free starter quota, check the console | `DASHSCOPE_API_KEY` | Alibaba Cloud |
| `openrouter` + a `:free` model | free, 50 requests a day (1000 after buying $10+ in credits) | `OPENROUTER_API_KEY` | OpenRouter and the model's provider |
| `openai_compatible` | depends on the server | `api_key_env` in the config, if needed | your server (`base_url`) |

Free tier terms change, check them on the provider's site before choosing.
Free cloud tiers may store requests, and your requests contain your code. If
that is not acceptable, use Ollama.

The free chat versions of claude.ai and ChatGPT don't provide an API, and
connecting to them through browser emulation violates their terms of use and
risks getting the account banned. GitHub Models, which used to give free
access to OpenAI models, was shut down on July 30, 2026.

#### Ollama: free and local

```bash
ollama pull qwen2.5-coder:7b     # ~4.7 GB; an 8 GB GPU is enough
```

```yaml
providers:
  strategy_generation: { provider: ollama, model: qwen2.5-coder:7b }
  semantic_diff:       { provider: ollama, model: qwen2.5-coder:7b }
  second_reviewer:     { provider: ollama, model: qwen2.5-coder:7b }
limits:
  max_parallel_ai_calls: 1   # one GPU, requests are queued anyway
```

`confidence-score doctor` checks that Ollama is running and the model is
downloaded.

Worth knowing:

- The context window is set automatically. By default Ollama uses 4,096
  tokens and silently truncates a longer prompt from the start, which is
  exactly where the diff is. The model then confidently rates code it has
  never seen. So the provider talks to Ollama's native API and requests a
  32,768-token window (`context_window` in the config). If the diff doesn't
  fit even into that, the answer is rejected with a reason in the report.
- Scores are reproducible: requests use `temperature: 0` and a fixed `seed`,
  so the same PR gets the same score.
- The first request is slow, about 45 seconds on an 8 GB GPU, while the model
  is loaded into video memory. After that the demo PR is checked in 4-7
  seconds, and a re-run of the same PR comes from the cache in a second.
- A 7B model is noticeably weaker than Opus. On the demo bug it notices the
  removed check but rates it `medium`, and on the safe refactoring it
  sometimes invents a change that isn't there. It is a free addition to the
  property tests, not a replacement for a strong reviewer.
- If `ollama pull` fails with `server gave HTTP response to HTTPS client`, the
  Ollama server reaches the internet around your VPN or proxy, and the CDN
  with model files is blocked on the direct route. Set `HTTPS_PROXY` for the
  Ollama app itself (or turn on the VPN client mode that captures traffic from
  all programs) and run `ollama pull` again.

### Reviewer panel

The second reviewer can be a panel of several models, for example a strong
cloud one and a free local one:

```yaml
providers:
  second_reviewer:
    - { provider: anthropic, model: claude-opus-5,    weight: 2 }
    - { provider: ollama,    model: qwen2.5-coder:7b, weight: 1 }
```

- The panel score is the weighted average of the members that answered. Each
  member's score is first capped by its own findings (no more than 40 for
  `high`, no more than 75 for `medium`) and only then goes into the average.
  A noisy member shifts the result but doesn't decide it.
- If someone didn't answer (no key, hit a rate limit, Ollama not running), the
  score is computed from those who answered, and the score cap drops in
  proportion to the weight of the silent members. The report shows who dropped
  out and why.
- A large disagreement, 40 points or more between scores, goes into the notes.
  It doesn't affect the score, but in that case the findings are worth reading
  yourself.
- Findings from all members are listed together, each marked with who found
  it. Similar findings from different models are not merged, because comparing
  free text is unreliable.
- `weight` defaults to 1. `label` sets the name in the report, by default
  `provider/model`. The same model listed twice is a config error.

Each member makes a separate model call, so a panel of three costs as much as
three reviewers. The semantic diff has no panel: it already makes as many calls
as there are functions.

### Model behavior worth knowing about

- A model may refuse a request based on a safety classifier decision. This
  occasionally happens with diffs about cryptography or network packet
  parsing. The provider enables server-side fallbacks, and the API retries the
  refused request on the recommended fallback model. If the whole chain
  refuses, the check for that function is skipped and counts neither as
  "unsafe" nor as "safe".
- The response limit leaves room for thinking. Claude Opus 5 has thinking on
  by default, and it shares `max_tokens` with the answer, so the limit is
  16,000 tokens even though the JSON itself is much smaller. Only tokens that
  were actually generated are billed.

## CLI

```bash
confidence-score init [--path confidence.yml] [--force]

confidence-score doctor [--repo PATH] [--config PATH]
  # which checks will run with the current keys, SDKs and Node.js, and what the
  # score cap will be; sends nothing to any API

confidence-score run
  --base REF            # base ref (default: merge-base with origin/main)
  --head REF|WORKTREE   # target ref (default: working tree)
  --repo PATH            # repository path (default: .)
  --config PATH          # path to confidence.yml (default: looked up in the repo)
  --format terminal|json|markdown
  --json-out PATH        # also save the JSON report
  --markdown-out PATH    # also save Markdown for a PR comment
  --fail-under N         # override thresholds.fail_below
  --no-gate              # always exit code 0 (informational only)
  --debug                # full traceback on error
```

The exit code is `1` if `hard_fail` triggered or `overall` is below
`thresholds.fail_below` (or `--fail-under`), otherwise `0`. The merge gate is
built on this: the GitHub Action below turns it into a required check.

## Configuration (`confidence.yml`)

The full schema with defaults and comments is in
[`confidence_scorer/config.py`](confidence_scorer/config.py) (`DEFAULT_CONFIG_TEMPLATE`),
the same file that `confidence-score init` creates. Main sections:

| Section | What it does |
|---|---|
| `languages` | which diff languages to analyze (`python`, `javascript`) |
| `exclude` | globs that are never analyzed (tests, vendor, dist...) |
| `weights` | weights of the three checks in the final score (normalized automatically) |
| `thresholds` | the `pass_at` / `warn_below` / `fail_below` boundaries |
| `hard_fail` | whether hard-fail is on and what score it caps the result at |
| `hypothesis` | `max_examples`, per-function and per-file timeouts, `seed` for Python property tests |
| `js` | the same for JS/TS (`fast_check_examples`, timeouts, `seed`, path to `node`) |
| `providers` | provider, model and `effort` for each of the three AI tasks; the second reviewer can be a panel (see [AI keys and models](#ai-keys-and-models)) |
| `limits` | maximum number of functions per run, size of the diff sent to AI |
| `evidence` | score cap when check coverage is incomplete (`min_cap`) |
| `cache` | disk cache of AI responses: re-running the same PR isn't billed again |
| `execute_changed_code` | `false` turns off executing the diff entirely (see [Security](#security)) |

The config only stores the names of the environment variables that hold the
keys, never the keys themselves.

## GitHub Action

Available on the
[GitHub Marketplace](https://github.com/marketplace/actions/confidence-scorer).

It takes two workflows: the first computes the score, the second posts the
report as a PR comment. For a PR from a fork GitHub gives neither secrets nor
write permissions, so the first workflow can't post the comment itself. The
second one runs in the context of your repository, doesn't download the PR
code and only publishes the finished report.

```yaml
# .github/workflows/confidence-score.yml
name: Confidence Score
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
jobs:
  score:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0   # required: base...head can't be compared without full history

      - uses: Kakadu525/confidence-scorer@v1
        with:
          post-comment: "false"
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          openrouter-api-key: ${{ secrets.OPENROUTER_API_KEY }}

      - name: Save PR number
        if: always()
        env:
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: printf '%s\n' "$PR_NUMBER" > pr-number.txt

      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: confidence-report
          path: |
            report.md
            pr-number.txt
          if-no-files-found: ignore
```

Copy the second workflow from this repository as is:
[`.github/workflows/confidence-comment.yml`](.github/workflows/confidence-comment.yml).

Don't replace `pull_request` with `pull_request_target`, even if comments
"don't work": that way code from a fork runs with access to your secrets.

If PRs only come from branches of the same repository, one workflow is
enough: remove `post-comment: "false"` and the artifact steps, and add
`pull-requests: write` to `permissions`.

Provider keys are passed through the inputs `anthropic-api-key`,
`openai-api-key`, `openrouter-api-key`, `deepseek-api-key`,
`dashscope-api-key`. The Action sets the outputs `score` and `verdict` and
fails when the merge gate triggers, so it can be made a required status check.
There is also a `gate` output (`passed` or `failed`). Outputs are available
even after the Action fails, in steps with `if: always()`. If you don't want to
block merges, set `fail-on-gate: "false"`: the Action will only report the
result.

### Free in CI

Ollama doesn't fit regular GitHub runners: there is no GPU, and the model would
have to be downloaded on every run. For CI, OpenRouter models with the `:free`
suffix work for free:

```yaml
providers:
  strategy_generation: { provider: openrouter, model: <model id>:free }
  semantic_diff:       { provider: openrouter, model: <model id>:free }
  second_reviewer:     { provider: openrouter, model: <another model id>:free }
limits:
  max_parallel_ai_calls: 1
```

Without purchased credits OpenRouter gives 50 free requests a day, and the
semantic diff spends one request per changed function. For an active
repository that isn't enough: either buy credits (the limit goes up to 1000 a
day) or keep only the second reviewer on OpenRouter.

Secrets aren't available in PRs from forks, so the AI checks are skipped there
and the score gets a coverage cap.

## Security

Differential testing imports and executes both versions of the code from the
PR. If the diff contains malicious code, it will run. There is no full sandbox
(a container without network and file access). So:

- run the check in CI on a disposable runner, not on your own machine;
- use `pull_request`, not `pull_request_target`;
- don't give the runner secrets the check doesn't need;
- in an untrusted environment set `execute_changed_code: false`: then the PR
  code is never executed, and the semantic diff and second reviewer remain.

Each file is tested in a separate process with per-function and per-file
timeouts. Input generators proposed by the model are never executed as code:
they go through a fixed list of allowed constructs.

The diff and the code of the changed functions are sent to the selected AI
providers. Free cloud tiers may store requests; if that is not acceptable, use
Ollama.

## Limitations

- Class methods don't take part in differential testing, only top-level
  functions. Calling a method needs an instance of the class, and building one
  automatically when the constructor has arbitrary dependencies is much
  harder. Methods are still checked by the semantic diff and the second
  reviewer.
- Nested functions and closures aren't extracted separately: their behavior
  can't be separated from the outer function.
- `*args`, `**kwargs` and rest parameters aren't supported by the input
  generator. Such functions get the `skipped` status instead of being dropped
  silently.
- Differential testing may find a divergence on degenerate inputs that break
  implicit preconditions. For example, `lo > hi` for a function that expects
  `lo <= hi`. That isn't always a bug in the everyday sense, but the behavior
  really did diverge, and a human decides how much it matters.
- The JS/TS worker limits time per function between property runs
  (`js.per_function_timeout_s`), but can't interrupt an infinite loop inside a
  single call: in Node, synchronous code can't be stopped from the same thread.
  The per-file timeout on the Python side handles that case.
- Non-deterministic functions are skipped. If a function gives different
  results for the same input (`random`, `time`, network), the divergence has
  nothing to do with the diff. Such a function gets the `skipped` status with
  an explanation.
- Rare "magic" values aren't found. A regression like
  `if n == 987654: return 0` won't be caught by random search: edge values are
  enumerated deterministically, but specific large constants aren't part of
  that enumeration. Every property-based approach works this way, and such
  cases are left to the semantic diff and the second reviewer.
- The semantic diff and the second reviewer depend on the quality of the model.
  They are probabilistic estimates, not formal verification.

## Development

```bash
pip install -e ".[dev]"
cd confidence_scorer/js_helpers && npm install && cd -   # for JS/TS tests

pytest -q                                   # tests
pytest -q --cov=confidence_scorer           # with coverage
ruff check confidence_scorer tests          # linter

# live test on a local Ollama (skipped by default)
CONFIDENCE_OLLAMA_MODEL=qwen2.5-coder:7b pytest -q -k live
```

JS/TS path tests are skipped if Node or the `js_helpers` dependencies aren't
installed. CI (`.github/workflows/tests.yml`) runs a Linux/Windows ×
Python 3.10/3.12 matrix. Windows is in the matrix because the per-function
timeout works differently there (no SIGALRM), and a regression in that spot
only shows up on it.

Project layout:

```
confidence_scorer/
  cli.py                     # click CLI: init / doctor / run
  doctor.py                  # check readiness: keys, SDKs, Node, score cap
  pipeline.py                # glues everything into one run
  config.py                  # pydantic schema for confidence.yml + init template
  presets.py                 # provider URLs and keys: ollama, deepseek, qwen, openrouter
  git_diff.py                # git diff -> list of changed files
  scoring.py                 # folds 3 sub-scores into overall + verdict
  extractors/
    python_extractor.py      # AST diff of changed functions (Python)
    js_extractor.py          # Babel diff of changed functions (JS/TS)
  checks/
    property_tests.py        # differential testing orchestration (Python)
    js_property_tests.py     # the same for JS/TS
    strategy_builder.py      # type hint -> Hypothesis strategy, AI spec allowlist
    js_strategy.py           # TS type -> spec (Python port for the orchestrator)
    semantic_diff.py         # AI: behavioral changes of a function
    second_reviewer.py       # AI: independent review of the whole diff
    review_panel.py          # reviewer panel: parallel runs and score aggregation
    _diff_worker.py          # subprocess worker: actually runs Python functions
    severity.py              # an AI's score can't exceed what its own findings allow
  ai/
    anthropic_provider.py    # Claude: fallbacks on refusals, cached system prompt
    openai_provider.py       # OpenAI and any OpenAI-compatible API (DeepSeek, Qwen, OpenRouter)
    ollama_provider.py       # local Ollama through the native API (context window in the request)
    cache.py                 # disk cache of responses (key = provider+model+effort+prompt)
    prompts.py               # prompts for the three AI tasks
  js_helpers/                # Node: extract.js, diff_worker.js, spec_to_arbitrary.js
  report/                    # terminal (rich) / markdown (PR comment) / json
action.yml                   # GitHub Action
.github/
  workflows/confidence-score.yml   # score for a PR
  workflows/confidence-comment.yml # comment with the report, including PRs from forks
  workflows/tests.yml        # project tests: Linux/Windows × Python 3.10/3.12 + ruff
example_demo/                 # reproducible demo (bug + safe refactoring)
```

## License

[MIT](LICENSE)
