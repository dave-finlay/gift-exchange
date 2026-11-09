# How the automation stops

Dependency updates are meant to flow from Dependabot to a published
image without anyone doing anything. This tracks the ways that flow
stops: which are deliberate, which are open questions, and what's being
done about them. Last reviewed 2026-11-09.

## What gates what

- **Merging** a pull request needs only CI's `test` job (the "main: CI
  must pass" ruleset). Dependabot's pull requests merge themselves once
  it passes, unless they contain a major update
  (`dependabot-auto-merge.yml`).
- **Publishing** `main`'s image needs the whole image job
  (`image.yml`): build, start, run behind Caddy, and a grype scan with no
  fixable high or critical vulnerability. Dependabot's merges don't
  start workflows, so what they merge is built and published by the
  daily run at 05:17 UTC.
- **Releasing** (`release.yml`) scans the published image again, with the
  same rule.

So a failing image job doesn't stop merges. It stops publishing, and
with it everything merged since the last image that passed.

## Deliberate

### A major update waits for review

Major updates (2.x to 3.x) wait for a person, because they can change
behaviour. Dependabot groups the week's updates into one pull request,
so a single major update holds back the whole group: Django 5.2.19's
security fixes waited behind django-axes 9 in #44.

Moving to a new Django or Python version is never automatic: Dependabot
stays within `pyproject.toml`'s ranges and on Python 3.13 images.

## Open questions

### A fixed vulnerability the base image hasn't caught up with

Debian fixes a package, grype's database learns of the fix, and every
scan fails, but the fix reaches the image only when Docker rebuilds
`python:3.13-slim`, which can take days. Merges carry on; publishing
stops until then, holding back every other fix merged in the meantime.

It happened on 2026-11-08: Debian's zlib fix for CVE-2026-76844 and
CVE-2026-85091. Both were assessed not affected (nothing the app runs
calls the vulnerable functions), but the gate doesn't know that, and the
scans failed while Django 5.2.19 waited to be published. The Dockerfile
now installs the fixed zlib as a stopgap, pinned to that version, and
the step fails the build once the base image has it, so it's removed
rather than left to pull in newer packages.

Whether this should stop publishing at all isn't settled. A finding
that was already in the last published image doesn't get worse by
publishing an image that also fixes other things. Options:

- **Read the VEX**: give grype the image's VEX (`--vex`), so findings
  assessed not affected don't count. This should happen whatever else
  is decided; it needs the VEX somewhere CI can fetch it.
- **Ratchet**: fail only on fixable findings the last published image
  didn't have. Old findings are still reported, and still need fixing.
- **Stopgap installs**, as for zlib: a fix, but work each time.

### A fix on a version line we can't take

grype's "fixable" means a fix exists in any version. For CPython it
reports fixes in 3.15 and 3.16, which the Python 3.13 image will never
get; a Django fix only in 5.3 would be the same, since Django stays on
5.2. Today these findings are medium or low, so they don't trip the
gate. A high one would stop publishing with nothing to merge. The
options above apply here too.

### An update that breaks the image but passes `test`

The image job runs on pull requests, but it isn't required, so a
Dependabot pull request that breaks the build, the start-up check or
the Caddy check still merges, and `main` stops publishing. Likely
sources: a new base image digest (#40 fixed one whose apt was blocked),
uv's image, or Caddy's.

Fix to consider: split the image job, and require the build and the
start-up and Caddy checks, but not the scan, so a broken image can't
merge but a newly reported vulnerability doesn't block merges.

### Steps that never run on pull requests

Publishing, the attestations and the SBOM run only on `main`, and the
release workflow only on version tags. A Dependabot bump of the actions
they use merges without having run, and is first tried by the next
daily run, or the next release.

## Safe, but holds things back

### A minor or patch update that breaks `test`

The pull request stays open and red, and holds back the rest of its
group. Likely sources: ruff (still 0.x; its minor releases add rules and
change formatting), a new Django deployment check (CI runs
`check --deploy --fail-level WARNING`), or hypothesis finding a new
failing example.

### Dependabot can't make a pull request

If an update can't be resolved within `pyproject.toml`'s ranges, or a
security fix needs a range changed (Dependabot only updates `uv.lock`),
or the version is one we ignore (Python 3.14 images), no pull request
appears. The only sign is Dependabot's log (Insights, Dependency graph,
Dependabot).

### Delays and outages

- Dependabot's merges are published by the next daily run, up to a day
  later. CI doesn't run on `main` for them either.
- grype's database download, GitHub's runners, the container registry
  or Docker Hub can fail a run. Run it again.

## Assumptions to keep an eye on

- The auto-merge workflow finds major updates by reading Dependabot's
  commit message (`update-type: version-update:semver-major`). If
  Dependabot changed that format, major updates would merge once `test`
  passed.
