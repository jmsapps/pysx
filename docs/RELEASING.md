# Releasing

The production identities are the Python distribution `pysx` and VS Code extension
`jmsapps.pysx`. Local VSIX builds use the same extension identity.

## Account setup

After obtaining ownership of the PyPI project, add a GitHub Trusted Publisher with
repository owner `jmsapps`, repository `pysx`, and workflow filename `release.yml`.
The workflow does not use a GitHub environment; leave that field empty.

In the Marketplace management page for publisher `jmsapps`, open Trusted Publishing
and create a policy with the same repository owner, repository and workflow filename.
Leave Environment empty. Both publishing jobs request short-lived OIDC credentials.
See [PyPI setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) and
[Marketplace setup](https://code.visualstudio.com/api/working-with-extensions/publishing-extension).

## Release procedure

Set matching versions in `pyproject.toml` and `editor/package.json`, refresh lockfiles,
and update the changelog. Run the complete verification manifest before tagging.
Create a `vMAJOR.MINOR.PATCH` tag at the intended commit. Only the repository owner
publishes its GitHub release. Publishing that release triggers `.github/workflows/release.yml`.
Draft releases and tag creation alone do not upload packages.

The workflow checks the tagged source, runs qualification, builds wheel, sdist and
VSIX, and retains them together with `release.json` hashes in the `release-candidate`
workflow artifact. Both publishing jobs depend on successful qualification and
consume that artifact. Inspect each publishing job separately after a run.

`editor/build_vsix.py` packages with the `@vscode/vsce` pin in
`editor/package.json`; the Marketplace validates the package manifest that
`code --install-extension` ignores, so the official packager produces it. That one
file is what the editor suites load, what `scripts/install-extension.sh` sideloads
and what `vsce publish --packagePath` uploads — no channel gets a repackaged copy.
Failed qualification uploads `.pysx-logs` as `release-qualification-logs`.

## Recovery

For a partially successful publication, rerun failed jobs against the same candidate
artifact. The two registries give different guarantees, and the rerun checks differ
accordingly.

PyPI publishes per-file sha256 digests. The retry step compares them against the
candidate hashes in `release.json`: identical content is skipped, conflicting content
fails the job. `skip-existing` covers only the minutes in which PyPI's CDN-cached
JSON API still reports a file it has already accepted; it never suppresses a digest
conflict, because that comparison fails the job before any upload is attempted.

The Marketplace does not publish a digest, and the package it serves back is not
promised to be byte-identical to the one uploaded. A rerun therefore establishes
only that the version exists — it reports `version already published; content
equivalence unverified` and leaves the candidate hash in `release.json` as the sole
record of what was built. Confirm the listing by hand when it matters.

Do not rebuild or replace a published version to resolve a conflict. Investigate the
retained candidate and registry files, then prepare a new version when the content
must change.

## Installation

After publication, install the Python package in a Python 3.14+ environment with
`uv pip install 'pysx[lsp]==0.1.0'`, then install `jmsapps.pysx` from Marketplace.
Select that Python environment in VS Code. For local installation, run
`scripts/install-extension.sh`; it builds the production VSIX and removes the former
`pysx-local.pysx-lang` extension. `scripts/uninstall-extension.sh` removes both ids.
Both scripts need a **Developer: Reload Window** afterwards. Existing `pysx.*`
settings remain available.

## First-release validation

Before the first live release, validate the workflow on supported runners and
rehearse artifact qualification without uploads. After the owner publishes, retain
the tag, commit SHA, workflow URL, artifact hashes and listing URLs. Verify clean
registry installations and retry behavior. Local tests alone do not establish live
registry authorization or successful publication.
