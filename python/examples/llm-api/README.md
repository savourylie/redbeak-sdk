# Evaluating an LLM API with Redbeak

The smallest integration Redbeak supports: your system under test is a model
endpoint, Redbeak sends it one question per case, and your adapter returns the
model's reply. There is no tool use, no business state, and no conversation.

[`adapter.py`](src/redbeak_example_llm_api/adapter.py) is the whole of it. Copy
it, change the HTTP call to match your provider, and it is your integration.

## Before you start

**Running an evaluation requires a Redbeak account.** These packages are a
client. They do not stand up a Redbeak service, and there is no standalone mode:
the suite, the answer key, and the scoring live in Redbeak Cloud, which is what
makes the evaluation a black-box one. You can read every line here without an
account; you cannot run a Run without one.

You will need:

* a Redbeak Project, and a CLI token for it, from the dashboard;
* a Target registered in that Project, with a TargetVersion that names the model
  you are evaluating;
* an API key for your model provider.

## The single-turn limit

Each case is one question and one answer. This adapter keeps nothing between
calls: no conversation history, no context carried from the previous case, no
state of any kind. Two cases running back to back are indistinguishable from two
cases run on different machines a week apart.

That is a deliberate limit, not an unfinished feature. A target that remembers a
conversation has per-session state, and state has to be isolated per case, reset
deterministically, and released — which is why `SingleTurnTextAdapter` supplies
`reset`, `observe` and `close` as no-ops and this example is one method long. If
your target holds a conversation, implement the five-method `TargetAdapter`
protocol directly and use `SessionStore`; do not extend this example with a
dictionary of past turns.

A scenario that sends a second turn to this adapter is refused with an
`AdapterConfigurationError` rather than answered without the first turn. A wrong
answer produced by a mismatched integration would otherwise be recorded as the
model's.

## What each side receives

| Recipient | Receives | Never receives |
| --- | --- | --- |
| **Your model provider** | The question text for the current case, and the `Authorization` header carrying your provider key | Redbeak's project, run, case, or scenario identifiers; the scenario name; anything from Redbeak other than the question itself |
| **Redbeak** | The model's user-visible reply, verbatim; an empty `observations` list; the protocol and timing metadata the runner adds | Your provider key; the provider's raw response body; provider error text, which routinely quotes the request back |
| **Your adapter** | One question at a time | The correct answer, the option key, scoring rules, future questions, or the rest of the suite |

The last row is enforced by the SDK, not promised by it: every input validates
against contract `0.1` and against the evaluation-private blocklist read out of
that contract, so evaluator-private material is refused rather than trusted not
to arrive.

Failures are reported by status code alone (`"the model provider could not serve
the request (HTTP 503)"`). That is not caution for its own sake — a provider's
error body frequently contains the prompt, and sometimes the key, and evidence
submitted to Redbeak must not carry either.

## Install

```bash
pip install redbeak-runner            # the CLI and the outbound runner
pip install ./python/examples/llm-api # this example, or your copy of it
```

`redbeak-runner` brings `redbeak-adapter-sdk` and `redbeak-contracts` with it.
The example adds only `httpx`, which the runner already installs.

## Configure the model provider

Three environment variables, read when the adapter is constructed:

```bash
export LLM_API_BASE_URL="https://api.openai.com/v1"  # any OpenAI-compatible endpoint
export LLM_API_KEY="..."                             # your provider key, not a Redbeak token
export LLM_API_MODEL="gpt-4o-mini"
export LLM_API_TIMEOUT_S="60"                        # optional; 60 by default
```

The call is `POST {LLM_API_BASE_URL}/chat/completions` with a single user
message, so any provider speaking the OpenAI chat API works by changing
`LLM_API_BASE_URL` — hosted services and a local server alike. For a provider
with a different shape, change `_ask_model` and leave the rest.

Check the wiring before claiming any work:

```bash
redbeak runner doctor --adapter redbeak_example_llm_api
```

If a variable is missing, the adapter refuses to be constructed at all, and the
command stops on a traceback whose last line names what to set:

```text
AdapterConfigurationError: set LLM_API_KEY to configure the model provider
```

Failing here is deliberate. The alternative — an adapter that constructs happily
and discovers the problem on its first call — would claim a real Run and then
fail every case in it identically.

## Four credentials, four jobs

Keeping these apart is the point of the design, so they are named separately
here rather than collectively as "your keys":

| Credential | Who issues it | What it authorises | Lifetime |
| --- | --- | --- | --- |
| **Project CLI token** | Redbeak dashboard | Creating and reading Runs in one Project | Until you revoke it |
| **Run-bound execution credential** | Redbeak, during `--execute` | Claiming and submitting cases *for that one Run* | The command's duration |
| **Model-provider key** | Your provider | Calling your model | Your provider's rules |
| **Project runner key** | Redbeak dashboard | Claiming work already queued; **not** creating Runs | Long-lived |

The model-provider key never reaches Redbeak, and no Redbeak credential ever
reaches your provider.

## Run the evaluation

Authenticate once against your Project:

```bash
redbeak auth login --base-url https://app.redbeak.example --project <project-uuid> --token-stdin
```

List what you can run:

```bash
redbeak runs versions
```

This reports the immutable suite versions available to your Project and the
Targets registered in it. Register the Target and its TargetVersion in Redbeak
before the first Run: that registration is what records *which model* the
results belong to. The adapter does not declare a target version of its own, so
it never narrows which Runs it is allowed to claim — `--target-version` on the
command below is what pins the identity.

Create the Run and execute it here:

```bash
redbeak runs create \
  --suite tmmlu_plus_50 --suite-version 0.1.0 \
  --target <target-name> --target-version <version> \
  --adapter redbeak_example_llm_api \
  --execute
```

> **About this suite.** `tmmlu_plus_50` version `0.1.0` is a 50-question
> Traditional Chinese sample drawn from TMMLU+, held and scored by Redbeak. Each
> case is one question with four options, and the prompt asks the model for a
> single letter. `--suite-version-id a4d606a1-6e6f-5bf8-b2a7-3ee00ce30076` pins
> the exact version instead of the slug and version pair; `redbeak runs versions`
> reports what your own Project actually has, which is the authority if it
> differs.
>
> Fifty questions drawn without subject balancing cannot support an accuracy,
> ranking or capability claim about any model. This suite verifies an
> integration.

Your Project CLI token authorises the Run's creation. `--execute` then obtains a
short-lived credential scoped to that Run alone, so a local runner started this
way cannot drain other queued work in the Project.

**The alternative:** a long-lived Project runner key can claim work that was
created in the dashboard.

```bash
redbeak runner start --adapter redbeak_example_llm_api --wait
```

That key claims queued work; it cannot create Runs. Use it when Runs are started
by someone else, or on a schedule. For this example, `runs create --execute` is
the path to follow.

## Reading the result

`runs create --execute` finishes when every case has been **submitted**. That is
not the same as the evaluation being **ready**: parsing and scoring happen in
Redbeak, after submission, and results appear in the dashboard when that
analysis completes. The command prints the Run's dashboard URL; open it there
rather than inferring the outcome from the CLI's exit.

Redbeak holds the answer key and does the scoring. This client never sees the
correct option and never computes a score, which is what lets the result be
evidence rather than a self-report.

In the dashboard you can expect, per case, the raw reply exactly as the model
produced it, the answer parsed from it, and the resulting verdict — plus the
aggregate counts. Three outcomes stay distinct there and should not be conflated:

* **a wrong answer** — the model answered, and answered incorrectly;
* **an unparseable answer** — the model answered in a shape the parser could not
  read, which is a fact about the model, not about your integration. The usual
  cause is a model that reasons aloud: a reply that weighs several options before
  concluding names more than one letter, so it is recorded as `invalid` rather
  than guessed at. It stays in the denominator and is never counted as a wrong
  answer;
* **an execution error** — your integration or the provider failed. Its `stage`
  names who has to fix it: `target_unavailable` is the provider, while `adapter`,
  `protocol`, `transport` and `timeout` are this client.

If most of a Run comes back `invalid`, the model is ignoring the single-letter
instruction rather than failing the questions. Read a raw reply before drawing
anything from the pass rate.

A completed integration does not require the model to score well. It requires
all 50 cases to be accounted for, the replies to be inspectable, and errors of
the third kind to be distinguishable from answers of the first two.

## What this example deliberately does not do

It does not know what TMMLU+ is. It does not select questions, format a
benchmark prompt, list the options, extract a chosen letter, or decide whether
an answer is right. All of that is in Redbeak, and keeping it there is what
makes the adapter reusable for the next suite without modification.

It also returns no observations. `observe()` answers with an empty list because
an API that answers questions changes no business state: there is nothing to
observe, and the user-visible answer is the whole of the evidence. An
integration whose target *does* change state — an order placed, a refund issued
— reports those facts as observations instead, and then Redbeak has something to
check beyond the text.

## Tests

```bash
uv run pytest python/examples/llm-api
```

The suite stubs the provider with `httpx.MockTransport`. It reaches no network,
needs no credential, and downloads no benchmark — the same rule the rest of this
repository follows.
