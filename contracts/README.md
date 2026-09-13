# Redbeak contract `0.1`

The JSON Schema documents in this directory are the **single source of truth**
for every payload that crosses the Redbeak boundary. TypeScript and Python both
load and validate against these files; neither language restates them as native
types, so the two cannot drift into disagreeing about what the wire accepts.

```text
contracts/
├── json-schema/0.1/   21 schemas, including the shared definitions
├── openapi/           runner-0.1.yaml, the four-endpoint runner surface,
│                      and cli-0.1.yaml, the separate CLI control-plane surface
└── fixtures/          valid and invalid examples consumed by both languages
```

`json-schema/0.1/` and `openapi/runner-0.1.yaml` are the frozen runner contract,
and everything below describes them. `openapi/cli-0.1.yaml` is a **different**
surface, versioned `cli 0.1`: the control-plane API the local CLI uses to create
and read Runs. It is authenticated by a CLI token rather than a runner key, its
payloads never cross into a customer runner, and it defines its schemas inline
rather than here — which is what keeps the boundary and secret guards below
applying to exactly the payloads they were written for.

## What the contract guarantees

Three properties are load-bearing, and each is enforced by the schemas rather
than by convention:

1. **One turn at a time.** A work claim reveals `sequence: 0` and the first user
   input. Later inputs arrive only as the `continue` next action of a turn
   submission. No cloud-to-runner payload has a field in which future turns
   could travel.
2. **No evaluation data crosses the boundary.** Ground truth, expected outcomes,
   rubrics, and scoring thresholds stay in Redbeak Cloud. An observation request
   carries fact _names_ only.
3. **Writes are idempotent.** `turn` and `complete` are keyed by
   `idempotency_key`, which is what lets a killed runner restart without
   duplicating evidence.

## Conventions

| Convention                                    | Why                                                                                                                                                                                                                                     |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pattern`, never a `format` assertion         | `format` is advisory by default in both ecosystems. A constraint enforced on one side only would break cross-language agreement. `format` is kept as an annotation; the paired `pattern` does the work.                                 |
| `additionalProperties: false` everywhere      | An unknown field is a contract violation, not something to tolerate. The one exception is documented below.                                                                                                                             |
| `schema_version` on envelopes, not components | Envelopes are HTTP bodies (`work-claim-*`, `turn-submission`, `next-action`, `case-completion`, `heartbeat-*`, the manifests). Components (`scenario-input`, `agent-output`, `observation`) are embedded and do not repeat the version. |
| snake_case                                    | Matches the payload examples already in `docs/architecture/demo-v0.md`.                                                                                                                                                                 |
| `x-redbeak-boundary` on every schema          | Records which side of the trust boundary a schema belongs to. Kept in the schema file so classification cannot drift from the thing it classifies.                                                                                      |

## The trust boundary

Every schema declares `x-redbeak-boundary`:

- **`runner`** — can cross into a customer environment. Subject to both the
  evaluation-private guard and the secret guard.
- **`internal`** — Redbeak Cloud evidence records (`run-manifest`,
  `evaluation-result`, `artifact-manifest`, `dataset-version`,
  `normalized-scenario`, `expected-evidence`, `suite-version`,
  `evaluator-fixture`). Never sent to a runner, but still subject to the secret
  guard: a credential must not be serialised into a run manifest, event, or
  artifact.
- **`shared`** — `common.schema.json`. Its definitions are pulled into
  runner-facing schemas by `$ref`, so it is guarded as if it crossed the
  boundary itself.

The guards are tests, not documentation. They fail on:

- a runner-facing schema that _declares_ an evaluation-private property, which
  `additionalProperties: false` cannot catch because the field is deliberate;
- any schema that declares a secret-like property (`lease_token` is the single
  allowlisted exception, spelled out in both languages so widening it needs a
  decision);
- a new schema added without a boundary classification;
- the blocklist itself being emptied.

### The one opaque payload

`work-claim-response` → `assignment.setup.payload` is the only place
`additionalProperties: false` cannot apply: it carries domain-shaped mock
environment state, and the contract must not know BFCL's shapes.

It is therefore typed as `common.$defs.leak_free_object`, which forbids
evaluation-private property names **at any depth**, including inside arrays.
Because the orchestrator validates its own outgoing payloads, a wholesale copy
of an upstream record that still carries `ground_truth` fails validation before
it is ever sent.

`path` is deliberately **absent** from the blocklist: it is a legitimate key in
a file-system mock. BFCL's evaluator-private `path` must be excluded by explicit
importer field mapping, which the field map in the architecture already
requires.

## Fixtures

```text
fixtures/<valid|invalid>/<schema-name>/<case>.json
```

The directory name is the schema name; both test suites discover fixtures by
walking the tree, so adding a file is enough to add a case to both languages.

Invalid fixtures cover four categories, and the tests assert coverage from the
**validator's own error keywords** rather than from filenames, so renaming a
fixture cannot quietly hollow out the evidence:

| Category                | Enforced by                                                    |
| ----------------------- | -------------------------------------------------------------- |
| Unknown fields          | `additionalProperties: false`, `propertyNames`                 |
| Missing required fields | `required`                                                     |
| Invalid state values    | closed `enum` / `const`                                        |
| Contradictory payloads  | `if`/`then` rules in `case-completion` and `evaluation-result` |

A contradictory payload is one whose every field is individually legal but whose
combination asserts two incompatible things — `status: "completed"` together
with an `error`, for instance. Left legal, such a payload makes the denominator
depend on which service happened to process it.

## Adding to the contract

1. Add `json-schema/0.1/<name>.schema.json` with `$schema`, `$id`, `title`,
   `description`, and `x-redbeak-boundary`. A missing annotation fails the tests.
2. Add at least one valid and one invalid fixture under each of
   `fixtures/valid/<name>/` and `fixtures/invalid/<name>/`, including an
   unknown-field case and a missing-required case.
3. Reference it from `openapi/runner-0.1.yaml` if it is a request or response body.
4. Run `make test`. Both languages must agree.

Adding a _new_ schema file is additive and needs no version bump. Changing an
existing wire payload — a new required field, a widened enum, a renamed
property — is breaking for a strict validator on the other end and does.

## Amendments to 0.1

`0.1` is otherwise frozen. One amendment has been made, and it is recorded here
rather than left for a reader to find by diffing, because the rule immediately
above says a widened enum normally earns a version bump.

### 2026-09-12 — single-turn benchmark content (TAI-218)

Four constraints were **relaxed**. Every document that validated before this
amendment still validates after it: no field was added, removed, renamed or made
required, and no value that used to be accepted is now rejected. The direction is
what made an in-place amendment defensible; a widening that rejected old data
would not have been.

| Change | Was | Now |
| --- | --- | --- |
| `common#/$defs/data_classification` | `["synthetic"]` | `["synthetic", "public_benchmark"]` |
| `common#/$defs/observation_request` `fact_names` | `minItems: 1` | `minItems: 0` |
| `normalized-scenario` `involved_classes` | `minItems: 1` | `minItems: 0` |
| `evaluator-fixture` `outcome_assertions.fact_names` | `minItems: 1` | `minItems: 0` |

All four have one cause. `0.1` was shaped around BFCL's multi-turn, stateful,
tool-using scenarios, where every case has a mock registry, an end state worth
observing, and synthetic content. A single-turn multiple-choice question
answered by a plain model API has none of those: no tool classes, no end state,
and openly licensed third-party content rather than generated content. The three
`minItems` relaxations let those emptinesses be stated as empty rather than
padded with a placeholder fact or class that does not exist — which would have
made every such case look as though it under-reported its evidence.

`public_benchmark` is **not** a route to real customer data. That remains
prohibited under either value until TaiwanEval's Pilot Data Handling Policy
defines its retention, deletion, access, incident and subcontractor boundaries,
and neither value may be used to label it.

Authorized by the repository owner on 2026-09-12, in preference to cutting a
`0.2` whose migration would have touched every schema, both language loaders and
all 132 fixtures. The amendment was made before the contract had any external
consumer.

### The `contract-v0.1` tag was moved

The tag was first cut on 2026-09-12 at `a84b8d7`, before this amendment, and
re-cut on 2026-09-13 at the commit that merged it. It was publicly fetchable for
roughly eighteen hours in between. Anyone who fetched it in that window holds a
ref that no longer matches this repository, and `python -m
redbeak_contracts.verify` will fail against that stale copy, naming the three
schemas in the table above. Re-fetch with `git fetch --tags --force`.

Moving a published tag is not the normal path, and this is not a precedent. It
was chosen over cutting a `0.2` because nothing outside this repository was known
to depend on `0.1`. Once anything does, an amendment gets a version bump instead:
the tag is the thing an auditor verifies against, so moving it a second time
would make that verification worth nothing.
