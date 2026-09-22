"""Every tunable the box has, declared once.

A setting used to live in four places at the same time: a constant in a module, a row in the settings
table read as a string, a name in an allow-list of writable keys, and a hand-written row of HTML. Adding
one meant touching all four, which is why the TMDB key ended up existing on the phone remote and nowhere
else. Here a setting is one `Setting(...)` and everything else follows from it: the typed read, the
validation on write, the schema the interface renders itself from, and whether an ordinary account is
allowed to see it at all.

Reads are cached, and the cache is keyed on a counter the database bumps on every write - so a value
changed by any code path, including one that never heard of this module, invalidates it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .. import db

log = logging.getLogger(__name__)

USER = "user"
DEVELOPER = "developer"
DEVELOPER_KEY = "developer.enabled"


@dataclass(frozen=True)
class Setting:
    """What a value is, what it may be, and who may see it."""

    key: str
    kind: type
    default: Any
    label: str
    group: str
    scope: str = USER
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    secret: bool = False
    help: str = ""

    def coerce(self, raw: Any) -> Any:
        """Stored text to a usable value. Raises ValueError with a reason a human can act on."""
        if raw is None or raw == "":
            return self.default
        try:
            if self.kind is bool:
                value: Any = str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif self.kind is int:
                value = int(float(str(raw)))
            elif self.kind is float:
                value = float(str(raw))
            elif self.kind is list:
                value = raw if isinstance(raw, list) else json.loads(str(raw))
            else:
                value = str(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"« {raw} » n'est pas un {_KIND_FR.get(self.kind, 'valeur')} valide.") from exc

        if self.choices and str(value) not in self.choices:
            raise ValueError(f"Valeur attendue parmi : {', '.join(self.choices)}.")
        if self.kind in (int, float):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"Minimum {_trim(self.minimum)}.")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"Maximum {_trim(self.maximum)}.")
        if self.kind is str and self.maximum is not None and len(value) > int(self.maximum):
            raise ValueError(f"{int(self.maximum)} caractères au maximum.")
        return value

    def store(self, value: Any) -> str:
        if self.kind is bool:
            return "1" if value else "0"
        if self.kind is list:
            return json.dumps(value, ensure_ascii=False)
        return str(value)


_KIND_FR = {bool: "oui/non", int: "nombre entier", float: "nombre", str: "texte", list: "liste"}


def _trim(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


# ---- the declarations -----------------------------------------------------
# Keys without a dot already exist in the database and keep their spelling, so nothing has to migrate.

DECLARED: tuple[Setting, ...] = (
    # --- what an owner sets ---
    Setting("tmdb_api_key", str, "", "Clé TMDB", "Bibliothèque", maximum=200, secret=True,
            help="Sans clé, les vignettes affichent les initiales du titre."),
    Setting("automount", bool, True, "Monter les disques branchés", "Bibliothèque",
            help="Détection et analyse automatiques des films sur un disque qu'on branche."),
    Setting("autoplay_next", bool, True, "Enchaîner les épisodes", "Bibliothèque"),
    Setting("device_name", str, "", "Nom du boîtier", "Système", maximum=64),
    Setting("parental_pin", str, "", "Code parental", "Système", maximum=12, secret=True),

    # --- the mode itself ---
    Setting(DEVELOPER_KEY, bool, False, "Mode développeur", "Système",
            help="Affiche les mesures et déverrouille les réglages fins. Le désactiver remet ces "
                 "réglages à leur valeur d'origine."),

    # --- search: constants that were frozen in the source ---
    Setting("search.timeout_seconds", float, 12.0, "Délai réseau", "Recherche", DEVELOPER,
            minimum=1.0, maximum=60.0,
            help="Au-delà, une source est considérée injoignable et signalée à côté des résultats."),
    Setting("search.default_limit", int, 30, "Résultats par recherche", "Recherche", DEVELOPER,
            minimum=5, maximum=200),
    Setting("search.max_limit", int, 200, "Résultats avec « Voir tout »", "Recherche", DEVELOPER,
            minimum=20, maximum=500),
    Setting("search.min_query", int, 2, "Lettres avant de chercher", "Recherche", DEVELOPER,
            minimum=1, maximum=5,
            help="En dessous, aucune requête n'est envoyée aux sources."),

    # --- catalogue cards ---
    Setting("cards.enrich_budget", int, 24, "Fiches enrichies par recherche", "Recherche", DEVELOPER,
            minimum=0, maximum=100,
            help="Au-delà, les cartes gardent les initiales. Monter ce chiffre multiplie les appels à TMDB."),
    Setting("cards.enrich_parallel", int, 6, "Enrichissements simultanés", "Recherche", DEVELOPER,
            minimum=1, maximum=16),
    Setting("cards.enrich_seconds", float, 10.0, "Délai d'enrichissement", "Recherche", DEVELOPER,
            minimum=1.0, maximum=30.0,
            help="Passé ce délai les fiches s'affichent telles quelles, la recherche n'attend pas."),

    # --- library ---
    Setting("library.page_size", int, 60, "Titres par page", "Bibliothèque", DEVELOPER,
            minimum=10, maximum=200),
    Setting("library.resume_min_seconds", int, 60, "Reprise à partir de", "Bibliothèque", DEVELOPER,
            minimum=5, maximum=600,
            help="Un titre regardé moins longtemps n'apparaît pas dans « Reprendre »."),

    # --- downloads ---
    Setting("downloads.poll_ms", int, 3000, "Rafraîchissement des téléchargements", "Téléchargements",
            DEVELOPER, minimum=500, maximum=60000),
    Setting("downloads.backoff_ms", int, 20000, "Attente si le client ne répond pas", "Téléchargements",
            DEVELOPER, minimum=2000, maximum=300000),

    # --- diagnostics ---
    Setting("dev.log_level", str, "INFO", "Niveau de journal", "Diagnostic", DEVELOPER,
            choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    Setting("dev.slow_query_ms", int, 50, "Seuil « requête lente »", "Diagnostic", DEVELOPER,
            minimum=1, maximum=5000,
            help="Une requête plus lente que ça est comptée dans les mesures."),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in DECLARED}

_cache: dict[str, Any] = {}
_seen_version = -1


def _fresh() -> dict[str, Any]:
    """The cache, emptied whenever anything wrote to the settings table."""
    global _seen_version
    version = db.settings_version()
    if version != _seen_version:
        _cache.clear()
        _seen_version = version
    return _cache


def get(key: str) -> Any:
    """The typed, validated value. A stored value that no longer passes falls back to the default."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise KeyError(f"réglage non déclaré : {key}")
    cache = _fresh()
    if key in cache:
        return cache[key]
    try:
        value = setting.coerce(db.get_setting(key, ""))
    except ValueError as exc:
        log.warning("setting %s is stored invalid (%s); using the default", key, exc)
        value = setting.default
    cache[key] = value
    return value


def put(key: str, raw: Any) -> Any:
    """Validate then store. An out-of-range value is refused rather than kept and misbehaving later."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise KeyError(f"réglage non déclaré : {key}")
    value = setting.coerce(raw)
    db.set_setting(key, setting.store(value))
    _cache.pop(key, None)
    return value


def developer_on() -> bool:
    return bool(get(DEVELOPER_KEY))


def visible(developer: bool | None = None) -> list[Setting]:
    """Developer settings are absent from the schema, not merely hidden in the page."""
    allowed = developer_on() if developer is None else developer
    return [s for s in DECLARED if s.scope == USER or allowed]


def schema(developer: bool | None = None) -> list[dict[str, Any]]:
    """What the settings screen renders itself from."""
    out = []
    for setting in visible(developer):
        value = get(setting.key)
        out.append({
            "key": setting.key,
            "kind": setting.kind.__name__,
            "label": setting.label,
            "group": setting.group,
            "scope": setting.scope,
            "help": setting.help,
            "default": setting.default,
            "minimum": setting.minimum,
            "maximum": setting.maximum,
            "choices": list(setting.choices),
            "secret": setting.secret,
            # A secret never travels back to the page: the interface only needs to know it is set.
            "value": bool(value) if setting.secret else value,
        })
    return out


def reset_scope(scope: str = DEVELOPER) -> list[str]:
    """Back to the declared defaults. Leaving developer mode must not leave the box in a state
    nobody can see any more."""
    cleared = []
    for setting in DECLARED:
        if setting.scope != scope or setting.key == DEVELOPER_KEY:
            continue
        if db.get_setting(setting.key, "") != "":
            db.set_setting(setting.key, "")
            cleared.append(setting.key)
    _cache.clear()
    return cleared
