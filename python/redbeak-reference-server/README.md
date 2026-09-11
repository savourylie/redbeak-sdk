# `redbeak-reference-server`

In-memory OpenAPI stand-in used by the runner tests and the local Demo v0
checkpoints. It never binds a socket: callers drive it through
`httpx.ASGITransport`.

This package is a test tool. It is **never published**, it is not part of a
customer `redbeak-runner` install, and no customer is meant to host a Redbeak
service from it.

## Why it lives in this repository anyway

Being in the repository and being in your install are different things.

The runner's protocol tests — leases, idempotency, authorization, resume,
pending submission, the walking skeleton — all import this package, and those
are exactly the tests that demonstrate the guarantees an auditor came to check:
resume without duplicate evidence, idempotent replay returning the original
response, and the outbound claim flow. Leaving the stand-in out would split the
runner's test suite in half and keep the important half somewhere you cannot
read, so this repository would assert those properties without showing how they
are verified.

What keeps that safe is that this is a test-time dependency of the repository,
not a runtime dependency of `redbeak-runner`. The runner never imports it;
`cli.py` reaches it only through a lazy import behind the opt-in
`--reference-server` flag; and the transport swap happens in the HTTP client, so
the code under test is byte-for-byte the code you run. `redbeak-runner`'s own
`tests/test_install_surface.py` asserts that importing the runner CLI loads no
`redbeak_reference_server`.

## What it does not model

**It models orchestrator behaviour, not orchestrator concurrency.** This is a
single-threaded behavioural model with no locking, so it cannot exercise
concurrent claims on the same case execution: two runners racing for one lease
is not a situation these tests can produce.

The production implementation uses a Postgres advisory transaction lock
(`pg_advisory_xact_lock`) for that. Concurrency correctness is covered by the
orchestrator's own acceptance suite, not here. Read the protocol guarantees
demonstrated in this package as single-actor guarantees.
