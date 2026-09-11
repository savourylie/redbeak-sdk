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

`REDBEAK_CONTRACTS_ROOT` wins when set, which is what lets an installed package
point at a checkout. Otherwise the package walks up from its own location until
a directory containing `contracts/json-schema` appears — which is how it
resolves both inside this repository and inside a monorepo that consumes it as a
submodule.

Runtime dependency: `jsonschema`. Nothing else.
