# ADR-0002 — Declare settings once, in a typed registry

## Status

Proposed — 2026-09-22

## Context

The owner asked for "des settings un peu de partout" and a developer mode holding many knobs that an
ordinary user must not see — while staying simple to look at.

Settings today live in three unrelated places, and each new one has to be added to all of them by hand:

- constants frozen in module source (`TIMEOUT_SECONDS`, `ENRICH_BUDGET`, `ENRICH_PARALLEL`, `RECENT_MAX`,
  `MIN_QUERY`, `DEFAULT_LIMIT`, poll intervals…), which cannot be changed without an edit and a restart;
- an untyped `settings` key/value table read through `db.get_setting(key, default)` returning strings;
- an allow-list of writable keys in `api/system_routes.py`, with per-key special cases.

The UI repeats the same knowledge a fourth time, as hand-written HTML per setting. That is why the TMDB
key existed on the phone remote and nowhere else: adding a setting means touching four files, so it gets
done once and forgotten.

## Decision

One declaration per setting, in `core/settings.py`, carrying everything anyone needs to know about it:

```python
Setting(
    key="search.timeout_seconds",
    kind=float, default=12.0, minimum=1.0, maximum=60.0,
    group="Recherche", label="Délai réseau",
    scope=DEVELOPER,
    help="Au-delà, une source est considérée injoignable.",
)
```

From that single declaration come:

- **Reading** — `settings.get("search.timeout_seconds")` returns a `float`, already coerced and clamped,
  cached in memory and invalidated on write. Module constants become registry lookups.
- **Writing** — one endpoint validates against `kind`, `minimum`, `maximum`, `choices`. An out-of-range
  value is refused with a reason rather than stored and misbehaving later.
- **The interface** — `GET /api/settings/schema` returns the declarations, and the settings panel renders
  itself from them. A new setting appears in the UI because it was declared, not because someone
  remembered to add a row.
- **Visibility** — `scope` is `USER` or `DEVELOPER`. Developer settings are omitted from the schema
  entirely unless developer mode is on, so they are not merely hidden in the page.

Developer mode is itself a setting, owner-only, and turning it off restores every developer knob to its
declared default rather than leaving the box in a state nobody can see or explain.

## Alternatives considered

- **Pydantic `BaseSettings`.** Excellent for configuration read once at boot from the environment, which
  is what `config.py` already does. It is the wrong shape here: these values change at runtime from the
  interface, and the schema has to be serialisable to the browser, which means describing the fields
  anyway.
- **A JSON file edited by hand.** No validation, no UI, and a syntax error takes the box down at boot on
  a machine with no keyboard attached.
- **Leave the constants in the source.** Honest and simple, and it is what the code does today — but it
  answers none of the request, and the measurement work showed these are exactly the values worth
  turning (enrichment budget, parallelism, timeouts, poll intervals).

## Consequences

Positive — a setting is declared in one place; the API, the validation and the interface follow. The
registry doubles as documentation of every tunable the box has, which no file currently provides.

Negative — an indirection between a constant and its use. A value read in a tight loop must be hoisted
rather than fetched per iteration, and the cache has to be invalidated on write or the box keeps serving
the old value until restart.

Neutral — the existing `settings` table stays as the storage; only the access path changes.

## Trade-offs

Discoverability and safe editing are bought with one lookup layer. The failure mode is chosen
deliberately: a bad value is refused at the door, so a setting can never put the box into a state that
only a reinstall clears.
