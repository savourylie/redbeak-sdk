"""Offline comparison of distributed schemas, their manifest, and a Git ref.

Run with ``python -m redbeak_contracts.verify --repository /path/to/redbeak-sdk``.
The default reference is the release's contract tag, never the working tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from redbeak_contracts import CONTRACT_VERSION, ContractError


def _schema_files(root: Traversable, prefix: str = "") -> Iterator[tuple[str, bytes]]:
    for child in root.iterdir():
        name = prefix + child.name
        if child.is_dir():
            yield from _schema_files(child, name + "/")
        elif child.name.endswith(".json"):
            yield name, child.read_bytes()


def _git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repository), *args], capture_output=True, check=False)
    if result.returncode:
        raise ContractError(result.stderr.decode().strip() or "Git comparison failed")
    return result.stdout


def verify(root: Traversable, repository: Path, ref: str) -> int:
    """Fail on absent, extra, or changed schemas, including a rewritten manifest."""
    manifest_path = root.joinpath("schema-manifest.json")
    if not manifest_path.is_file():
        raise ContractError("no packaged manifest; install a wheel or pass --wheel")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packaged = dict(_schema_files(root.joinpath("json-schema")))
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in packaged.items()}
    if not hashes or manifest != {"algorithm": "sha256", "schemas": hashes}:
        raise ContractError("packaged schemas differ from their SHA-256 manifest")

    # Resolve once so a moving ref cannot mix multiple revisions during the check.
    try:
        commit = _git(repository, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    except ContractError as exc:
        raise ContractError(f"cannot resolve contract reference {ref}: {exc}") from exc
    revision = commit.decode().strip()
    prefix = "contracts/json-schema/"
    paths = _git(repository, "ls-tree", "-r", "--name-only", "-z", revision, "--", prefix)
    source = {
        path.decode().removeprefix(prefix): _git(repository, "show", f"{revision}:{path.decode()}")
        for path in paths.split(b"\0")
        if path.endswith(b".json")
    }
    if packaged != source:
        changed = sorted(
            name
            for name in packaged.keys() | source.keys()
            if packaged.get(name) != source.get(name)
        )
        raise ContractError(f"packaged schemas differ from {ref}: {', '.join(changed)}")
    return len(packaged)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--ref", default=f"refs/tags/contract-v{CONTRACT_VERSION}")
    parser.add_argument("--wheel", type=Path, help="audit a wheel without installing it")
    args = parser.parse_args()
    try:
        if args.wheel:
            with zipfile.ZipFile(args.wheel) as archive:
                count = verify(
                    zipfile.Path(archive, "redbeak_contracts/_contracts/"),
                    args.repository,
                    args.ref,
                )
        else:
            # Deliberately ignore REDBEAK_CONTRACTS_ROOT and the editable marker.
            count = verify(
                files("redbeak_contracts").joinpath("_contracts"), args.repository, args.ref
            )
    except (ContractError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Contract verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {count} packaged schemas and SHA-256 manifest against {args.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
