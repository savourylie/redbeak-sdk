# Redbeak SDK

Redbeak asks you to run its client inside your own environment. This repository
is that client, published so you can read it before you run it.

The architecture makes specific claims about this code: outbound-only, no
inbound listener, no source, prompt or credential collection, no ground truth
received. Each one is checkable by reading what is here.

Running an evaluation requires a Redbeak account. These packages are a client.
They do not stand up a Redbeak service and are not intended to.

## What to read, and in what order

The repository is deliberately small enough to read in an afternoon.

| If you want to check | Read |
| --- | --- |
| What can cross the boundary at all | [`contracts/`](contracts/README.md) — the JSON Schema documents are the source of truth |
| That nothing listens on a port | [`python/redbeak-runner`](python/redbeak-runner/README.md) — four runtime dependencies, all outbound |
| What your adapter is handed, and what it is refused | [`python/redbeak-adapter-sdk`](python/redbeak-adapter-sdk/README.md) |
| That the schemas are enforced rather than described | [`python/redbeak-contracts`](python/redbeak-contracts/README.md) |
| How the protocol guarantees are tested | [`python/redbeak-reference-server`](python/redbeak-reference-server/README.md) — a test tool, never published |
| What an integration actually looks like | [`python/examples/llm-api`](python/examples/llm-api/README.md) — a complete single-turn adapter, one method long |

```text
contracts/            The versioned wire contract. Code-free and language-neutral.
├── json-schema/0.1/  Single source of truth for every boundary payload
├── openapi/          runner-0.1.yaml (the runner surface) and cli-0.1.yaml
└── fixtures/         Valid and invalid examples the test suites consume
python/
├── redbeak-contracts/         Loader, validator, and boundary guards
├── redbeak-adapter-sdk/       The TargetAdapter contract your integration implements
├── redbeak-runner/            The outbound runner: leases, idempotent evidence, resume
├── redbeak-reference-server/  Test-only orchestrator stand-in. Never published.
└── examples/llm-api/          A single-turn adapter for a plain LLM API, ready to copy
```

`contracts/` sits at the root, with no code beside it, because a future
TypeScript or Java SDK belongs in this same repository rather than a new one.
There are no placeholder directories for languages that do not exist yet.

## The boundary

The adapter SDK validates every input against contract `0.1` and refuses
evaluator-private material, so an adapter never receives ground truth, expected
outcomes, rubrics, thresholds, or future turns. Two schemas are annotated
`x-redbeak-boundary: internal` and are present so you can see what is kept away
from the runner, not because the runner ever receives them.

Raw evidence is preserved before parsing or scoring. Target failures stay
distinct from Redbeak infrastructure failures.

## Versioning and compatibility

The contract and each language package version independently:

```text
contract-v0.1    the wire contract
python-v0.1.0    the Python packages
```

Every release of the Python packages implements exactly one contract version,
exported as `redbeak_contracts.CONTRACT_VERSION` and asserted by the test suite.
`python-v0.2.0` may implement `contract-v0.1`; the numbers do not track.

The schemas are shipped inside the wheel, with a manifest of their SHA-256
digests. Those digests match the files tagged `contract-v0.1` in this
repository. You can verify the contract your installed package enforces without
trusting us.

Before 1.0 no compatibility is promised across contract minor versions.

## Working in this repository

Prerequisites: [uv](https://docs.astral.sh/uv/). The Python version is pinned by
`.python-version`; uv installs that interpreter itself.

```bash
uv sync --all-packages
make check               # formatting, lint, types, tests, distribution checks; offline
```

Nothing here needs a credential, a network connection, or a model provider. If a
check in this repository ever needs one, the contract has grown a dependency it
should not have.

While developing against a checkout rather than an installed wheel,
`REDBEAK_CONTRACTS_ROOT` points `redbeak_contracts` at a `contracts/` directory
of your choosing. Editable installs made by `uv sync` record the live source
directory at build time, so schema edits take effect without rebuilding the
package. Regular installs read bundled resources through `importlib.resources`.
Fixtures remain source-only and are never included in either distribution.

## Build and verify a distribution

After installing the development dependencies, build offline:

```bash
uv build --offline --package redbeak-contracts --out-dir dist
```

This produces an sdist and a wheel built from that sdist in an isolated PEP 517
environment. The build hook copies only `contracts/json-schema/`, and emits
`redbeak_contracts/_contracts/schema-manifest.json` inside the wheel (under
`src/` in the sdist). It records `algorithm: sha256` and a `schemas` mapping from
paths such as `0.1/common.schema.json` to SHA-256 digests of the exact file bytes.
The schemas themselves are at `redbeak_contracts/_contracts/json-schema/`.

To audit an installed package, obtain a clone of this repository with the
`contract-v0.1` tag and use the Python interpreter from that installation:

```bash
python -m redbeak_contracts.verify --repository /path/to/redbeak-sdk
```

The command first hashes every bundled schema and checks the entire manifest,
then compares every file byte-for-byte against Git objects at `contract-v0.1`.
It does not compare against the clone's working files, and it ignores
`REDBEAK_CONTRACTS_ROOT` so a development override cannot hide a broken install.
Missing, extra, or changed schemas, a changed manifest, and a missing tag all
produce a nonzero exit status. This comparison requires Git but no network.
It proves agreement with the selected Git ref; the manifest is not a signature.

To check a wheel before installing it:

```bash
uv run --offline --no-sync python -m redbeak_contracts.verify \
  --repository . --wheel dist/redbeak_contracts-0.1.0-py3-none-any.whl
```

`make contract-release-check` builds and runs that comparison. `contract-v0.1`
was tagged on 2026-09-12 and re-cut on 2026-09-13 to carry the amendment recorded
in [`contracts/README.md`](contracts/README.md); in a clone that lacks the tag
this command fails explicitly rather than falling back to another ref. During development, add `--ref HEAD` to compare against the
committed schemas without claiming release-tag verification. `make check`
exercises that comparison, rejects deliberate drift, installs all three customer
packages into clean virtualenvs, and validates contract fixtures and SDK/runner
behavior. It also checks the release tag when that tag is available locally.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
