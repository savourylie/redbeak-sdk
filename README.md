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

```text
contracts/            The versioned wire contract. Code-free and language-neutral.
├── json-schema/0.1/  Single source of truth for every boundary payload
├── openapi/          runner-0.1.yaml (the runner surface) and cli-0.1.yaml
└── fixtures/         Valid and invalid examples the test suites consume
python/
├── redbeak-contracts/         Loader, validator, and boundary guards
├── redbeak-adapter-sdk/       The TargetAdapter contract your integration implements
├── redbeak-runner/            The outbound runner: leases, idempotent evidence, resume
└── redbeak-reference-server/  Test-only orchestrator stand-in. Never published.
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
uv run pytest             # the whole suite, offline
uv run ruff check .
uv run mypy
```

Nothing here needs a credential, a network connection, or a model provider. If a
check in this repository ever needs one, the contract has grown a dependency it
should not have.

While developing against a checkout rather than an installed wheel,
`REDBEAK_CONTRACTS_ROOT` points `redbeak_contracts` at a `contracts/` directory
of your choosing. Unset, it walks up from its own location and finds the one
above.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
