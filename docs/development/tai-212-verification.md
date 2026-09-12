# TAI-212 distribution verification

Verified locally on 2026-09-12, from SDK base commit
`80b7a70a445deeb81734fece0a32163de2f0e2a1`, with the uncommitted TAI-212 change.

## Outcome and scope

Customers can install the three Python packages from wheels and validate
contract `0.1` outside any checkout. Both the direct wheel and a wheel rebuilt
from the sdist contain all 21 canonical schemas and their SHA-256 manifest.
Fixtures are absent. Editable workspace installs continue to read the live
source, including when the SDK is consumed under the monorepo's `sdk/` path.

The auditor command verifies both the manifest and the actual packaged bytes
against Git objects, independently of a runtime source override. Rewriting a
schema and its manifest together does not bypass comparison with committed
source. The public README documents the resource locations and commands.

## Evidence

- SDK `make check`: passed, including 377 Python tests, formatting, lint, and
  strict type checking. `UV_OFFLINE=1` is enforced by the Makefile.
- The distribution tests use actual `pip install --no-index --no-deps` into new
  virtualenvs outside the checkout. Runtime dependencies are installed from the
  uv cache, and `pip check` passes.
- Both distribution paths accept/reject the full 132-file contract corpus,
  construct and validate SDK models, and validate runner claim requests and
  responses through an in-process HTTP mock. No network service is started.
- Installed runtime distributions contain none of `starlette`, `uvicorn`,
  `fastapi`, or `redbeak-reference-server`.
- Direct ZIP imports load and validate bundled resources, without assuming
  resources are filesystem paths.
- Overrides take precedence; an invalid override fails explicitly. Editable
  source paths and fixture access remain intact.
- Direct-wheel and sdist-rebuilt manifests are identical. The manifest's own
  SHA-256 is `956a85b192c73bec0fbf0b5671af5e3797bc06baf2ef2867a549e636db3fc629`.
- A tampered sdist fails to build; a tampered packaged schema fails manifest
  verification; updating that manifest still fails comparison against Git.
- A disposable Git repository exercises a real `contract-v0.1` tag comparison
  and proves working-tree edits cannot influence its result.
- The consuming monorepo at `ba07a2f` passed its full offline `make check` with
  this SDK substituted in a disposable clone: 684 Python tests, 365 TypeScript
  tests, source/drift checks, type checking, and builds. Its existing checkouts
  and SDK pin were not modified. Integration caught and resolved a build-hook
  type-check dependency on Hatchling outside the isolated build environment.

## Remaining release acceptance

The actual SDK repository has no `contract-v0.1` tag. This was deliberately
deferred in TAI-214. `make contract-release-check` builds successfully and then
fails on that missing reference; comparison against `HEAD` passes for all 21
schemas. A synthetic test tag is evidence for the checker, not evidence of an
actual release tag.

Create the agreed contract tag as a release action and rerun
`make contract-release-check` before claiming tag-based provenance. CI checks
the real tag whenever present, and reports its absence otherwise. No index
publication, tag publication, remote CI execution, merge, or tracker completion
is established by this local verification.
