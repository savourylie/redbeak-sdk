# Redbeak contract `0.1`

The JSON Schema documents in this directory are the **single source of truth**
for every payload that crosses the Redbeak boundary. TypeScript and Python both
load and validate against these files; neither language restates them as native
types, so the two cannot drift into disagreeing about what the wire accepts.

```text
contracts/
├── json-schema/0.1/   19 schemas, including the shared definitions
├── openapi/           the four-endpoint runner surface
└── fixtures/          valid and invalid examples consumed by both languages
```

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
  `normalized-scenario`, `expected-evidence`). Never sent to a runner, but still
  subject to the secret guard: a credential must not be serialised into a run
  manifest, event, or artifact.
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
