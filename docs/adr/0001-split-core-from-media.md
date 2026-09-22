# ADR-0001 — Separate a reusable core from the media domain

## Status

Proposed — 2026-09-22

## Context

The owner wants the backend to serve a second project, but does not yet know what that project is.
That is the central constraint: there is no second set of requirements to design against, so any
abstraction invented now would be a guess, and guesses calcify.

What can be established is factual. A dependency graph of the 49 modules (`ast` walk over every
relative import) shows the foundations and the leaks:

| Module | Imported by |
|---|---|
| `db` | 21 |
| `config.SETTINGS` | 20 |
| `services.accounts` | 7 |
| `api.state` | 7 |
| `services.security` | 4 |

Against a candidate core of `db`, `config`, `accounts`, `security`, `files`, `textutil`, `guard`,
`state`, only **two** modules reach into the media domain:

- `api.state` — the `AppState` dataclass holds `LibraryJobs`, `Player` and `QBittorrent` by concrete type.
- `services.files` — imports `storage.Volume` and `storage.file_volumes`.

Everything else already points the right way: the media services depend on the core, never the reverse.

## Decision

Split the package along the dependency direction that already exists, and fix only the two edges that
contradict it.

```
aura/
  core/      db, config, accounts, security, settings registry, jobs, http, guard, api scaffold
  media/     library, catalog, player, torrents, metadata, storage, epg
  api/       routers, thin: they translate HTTP to calls on the two layers above
```

The rule is one-directional and checkable: **`core` must never import from `media`.** A test asserts it,
so the boundary cannot rot silently the way undocumented conventions do.

For the two leaks:

- `AppState` stops naming concrete media classes. It holds a small registry of named components that the
  application lifespan fills in. `core` then knows *that* there are components, not *which*.
- The volume concept moves into `core` as a plain "a place on disk with a label and a mount state".
  Media keeps the part that knows about films: which volumes hold a library, what to scan.

## Alternatives considered

- **Extract a separate installable package now.** Rejected: without the second project's requirements,
  the package's API would be designed against an imaginary consumer, and every later change to it would
  be a breaking change to a published interface for no benefit. The folder split gives the same
  separation and can become a package the day a second consumer exists.
- **Leave the layout alone and document the intent.** Rejected: the two leaks show conventions alone do
  not hold. `AppState` acquired media types precisely because nothing objected.
- **Hexagonal architecture with ports and adapters throughout.** Rejected as over-engineering for a
  single-process appliance: it would add an indirection layer per service to solve a coupling problem
  that the measurement shows is limited to two modules.

## Consequences

Positive — the boundary is explicit, enforced by a test, and the day a second project appears, `core`
is lifted out as a package rather than archaeologically reconstructed.

Negative — imports change across the codebase, which makes one large mechanical diff and will conflict
with any work in flight. Module paths in documentation and in the systemd units must follow.

Neutral — no runtime behaviour changes. This is a move, not a rewrite: the 155 tests must pass at every
step, and any test that needs editing beyond its import lines is evidence the move went too far.

## Trade-offs

Being able to lift the core out later is bought with one disruptive rename now. The alternative — waiting
for the second project to reveal itself — costs nothing today but means doing the untangling under the
pressure of an actual deadline, with a second consumer already depending on the wrong shape.
