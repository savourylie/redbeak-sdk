# `redbeak-contracts`

Loader, validator, and boundary guards for the versioned Redbeak wire contract.

The JSON Schema documents under [`contracts/json-schema/`](../../contracts/) are
the single source of truth. This package does not restate them as Python types:
it loads them and validates against them, so the TypeScript and Python sides
cannot drift into disagreeing about what the wire accepts.

```python
import redbeak_contracts as rc

rc.CONTRACT_VERSION  # "0.1" — the one contract version this release implements
rc.schema_names()  # every schema in the contract
rc.boundary_of("observation")  # "runner" | "internal" | "shared"
rc.validate("observation", document)
```

## What it enforces

- **Boundary classification.** Every schema carries an `x-redbeak-boundary`
  annotation, recorded in the schema file itself rather than in a list here, so
  adding a schema cannot silently add an unclassified one. A schema annotated
  `internal` is evaluator-private and must never reach a runner or an adapter.
- **The secret guard.** A set of property names that must never be serialised
  into any contract payload. `lease_token` is the one deliberate exception: the
  runner protocol cannot work without it, it is a short-lived capability scoped
  to a single case execution rather than a credential at rest, and the
  architecture already requires it to be excluded from application logs.
- **Format assertion is off, deliberately.** Every constraint that matters is
  expressed as a `pattern`, because `format` is advisory by default in both
  ecosystems and enabling it on one side alone would make the two validators
  disagree.

## Locating the schemas

`REDBEAK_CONTRACTS_ROOT` wins when set and points at a directory containing
`json-schema/`. Otherwise a regular installation uses its own resources at
`redbeak_contracts/_contracts/json-schema/`, loaded through `importlib.resources`.
`contracts_root()` and `schema_dir()` return resource `Traversable` objects,
which support `joinpath`, `iterdir`, `read_text`, and `read_bytes`; they need not
be filesystem paths (for example when importing from a ZIP archive).

Editable builds record the source `contracts/` path in an excluded development
resource. This keeps the repository and consuming monorepo reading the live
canonical files. Moving a checkout requires running `uv sync --reinstall-package
redbeak-contracts` again. Fixtures are available only in source development or
through the explicit override; they are not distributed.

Both sdist and wheel contain a SHA-256 manifest at
`redbeak_contracts/_contracts/schema-manifest.json` (under `src/` in the sdist).
The [public verification procedure](../../README.md#build-and-verify-a-distribution)
compares it and every bundled schema against Git objects at `contract-v0.1`.

Runtime dependency: `jsonschema`. Nothing else.
