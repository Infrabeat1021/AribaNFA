"""Non-secret application configuration.

Secrets never live here — they go to Windows Credential Manager via
`secrets_store`. This file is safe to open, inspect and hand to a colleague.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import APP_NAME
from .errors import ConfigError
from .model import Entity

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets" / "letterheads"
MAPPING_FILE = PROJECT_ROOT / "mapping" / "nfa_mapping.json"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def _query_params(value: str) -> dict[str, str]:
    """Pull the query parameters out of a pasted URL or path fragment."""
    text = (value or "").strip()
    if "?" not in text:
        return {}
    return {k: v[0] for k, v in parse_qs(text.split("?", 1)[1]).items() if v}


def normalise_host(value: str) -> str:
    """Reduce whatever was pasted to a bare `https://host`.

    People paste a full endpoint URL into a host box, which would otherwise be
    concatenated with a path and produce a hostname that cannot resolve.
    """
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlsplit(text)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path.split("/")[0]
    return f"{scheme}://{host}".rstrip("/")


def normalise_api_path(value: str) -> str:
    """Reduce whatever was pasted to just the service path segment.

    The app appends the environment, `/events/{id}` and the realm itself, so the
    setting must stop at the service version - e.g. `/api/sourcing-event/v2`.
    A pasted full endpoint is trimmed back to that rather than being rejected,
    because copying a working URL out of the developer portal is the natural
    thing to do and the resulting failure (a hostname that will not resolve)
    looks nothing like the cause.
    """
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return ""

    if "://" in text:                       # a full URL: keep only the path
        text = urlsplit(text).path
    text = text.split("?")[0].split("#")[0]

    lowered = text.lower()
    marker = lowered.find("/events")
    if marker != -1:                        # drop /events/{id}/... and beyond
        text = text[:marker]

    text = "/" + text.strip("/")
    for env in ("/prod", "/sandbox", "/test"):
        if text.lower().endswith(env):      # the app adds the environment
            text = text[: -len(env)]
            break
    return text.rstrip("/")


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME


def local_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


CONFIG_PATH = app_data_dir() / "config.json"
DRAFTS_DIR = app_data_dir() / "drafts"
LOG_DIR = local_data_dir() / "logs"
#: Dumps can contain supplier pricing, so they live outside the project folder -
#: the project folder is the thing that gets zipped and passed to a colleague.
DUMPS_DIR = Path.home() / "Documents" / APP_NAME / "dumps"


@dataclass
class EntityConfig:
    name: str
    address: str = ""
    logo: str = ""
    #: Set False for a wordmark logo that already shows the company name.
    show_name: bool = True
    logo_width_cm: float = 4.2

    def to_entity(self) -> Entity:
        logo_path = None
        if self.logo:
            candidate = Path(self.logo)
            if not candidate.is_absolute():
                candidate = ASSETS_DIR / self.logo
            logo_path = candidate
        return Entity(
            name=self.name,
            address=self.address,
            logo_path=logo_path,
            show_name=self.show_name,
            logo_width_cm=self.logo_width_cm,
        )


#: The InfraBeat logo is a wordmark, so `show_name` is False - the logo already
#: spells the name out. The address stays blank until a real one is supplied; a
#: wrong address on an approval document is worse than none.
DEFAULT_ENTITIES = [
    EntityConfig(
        name="InfraBeat",
        address="",
        logo="infrabeat.png",
        show_name=False,
        logo_width_cm=3.8,
    ),
]


@dataclass
class AppConfig:
    # --- Ariba connection -------------------------------------------------
    realm: str = ""
    #: Datacenter-specific. api.ariba.com is the US host; confirm yours with
    #: your Ariba administrator before debugging a token failure, because a
    #: wrong-host 401 is indistinguishable from a bad credential.
    oauth_base: str = "https://api.ariba.com"
    api_base: str = "https://openapi.ariba.com"
    #: Path segment on the Event Management API: "sandbox" or "prod".
    api_env: str = "sandbox"
    event_api_path: str = "/api/sourcing-event/v2"
    #: The Event Management API requires an integration user on every request,
    #: not just the realm — without it Ariba replies 400 "The user parameter is
    #: missing." It is a username, not a credential, so it belongs here.
    api_user: str = ""
    password_adapter: str = "PasswordAdapter1"

    # --- Commercial conventions ------------------------------------------
    gst_rate: str = "18"
    #: Whether a single quoted figure from Ariba already includes GST. Getting
    #: this wrong misstates every comparison row while looking plausible, so it
    #: is an explicit setting rather than an inference.
    gst_inclusive: bool = False
    top_n_vendors: int = 3

    # --- Local behaviour --------------------------------------------------
    #: Where the field mapping is read from. Point every install at one file on
    #: a shared drive and a mapping fix reaches the whole team without a server
    #: - which is the main thing a central portal would otherwise buy you.
    #: Blank means the copy inside the project folder.
    mapping_file: str = ""
    output_dir: str = str(Path.home() / "Documents" / "NFA")
    entities: list[EntityConfig] = field(default_factory=lambda: list(DEFAULT_ENTITIES))
    open_in_word: bool = True

    # --- Network overrides (unused today; kept for a policy change) --------
    proxies: dict[str, str] = field(default_factory=dict)
    ca_bundle: str = ""

    # ---------------------------------------------------------------------

    @property
    def gst_rate_decimal(self) -> Decimal:
        try:
            return Decimal(str(self.gst_rate))
        except Exception:
            return Decimal("18")

    @property
    def event_api_base(self) -> str:
        host = normalise_host(self.api_base)
        path = normalise_api_path(self.event_api_path)
        return f"{host}{path}/{self.api_env}"

    @property
    def mapping_path(self) -> Path:
        """The mapping file in use: a shared one if configured, else the local copy.

        Falls back to the local copy when a shared path is unreachable - a
        disconnected VPN must not stop someone generating a document.
        """
        if self.mapping_file:
            shared = Path(self.mapping_file)
            if shared.exists():
                return shared
        return MAPPING_FILE

    @property
    def mapping_is_shared(self) -> bool:
        return bool(self.mapping_file) and self.mapping_path != MAPPING_FILE

    def request_params(self) -> dict[str, str]:
        """Query parameters Ariba requires on every Event Management call."""
        params = {"realm": self.realm}
        if self.api_user:
            params["user"] = self.api_user
        if self.password_adapter:
            params["passwordAdapter"] = self.password_adapter
        return params

    def normalise(self) -> AppConfig:
        """Clean up pasted hosts and paths. Safe to call repeatedly."""
        # A pasted endpoint often carries the user and passwordAdapter that
        # Ariba requires. Harvest them before the path is trimmed, so pasting a
        # working URL configures the app rather than silently losing the parts
        # that made it work.
        for source in (self.event_api_path, self.api_base):
            harvested = _query_params(source)
            if not self.api_user and harvested.get("user"):
                self.api_user = harvested["user"]
            if harvested.get("passwordAdapter"):
                self.password_adapter = harvested["passwordAdapter"]

        self.api_base = normalise_host(self.api_base)
        self.oauth_base = normalise_host(self.oauth_base)
        self.event_api_path = normalise_api_path(self.event_api_path)
        self.realm = (self.realm or "").strip()
        self.api_env = (self.api_env or "sandbox").strip().lower()
        self.api_user = (self.api_user or "").strip()
        self.password_adapter = (self.password_adapter or "").strip()
        return self

    def entity_names(self) -> list[str]:
        return [e.name for e in self.entities]

    def entity(self, name: str | None = None) -> Entity:
        if not self.entities:
            raise ConfigError(user_message="No letterhead entity is configured.")
        for candidate in self.entities:
            if candidate.name == name:
                return candidate.to_entity()
        return self.entities[0].to_entity()

    def is_connection_ready(self) -> bool:
        return bool(self.realm and self.oauth_base and self.api_base)

    # ---------------------------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        self.normalise()
        path = Path(path or CONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path


def load_config(path: Path | None = None) -> AppConfig:
    """Load config, falling back to defaults when it is absent or unreadable.

    A corrupt config must never stop the app starting — the user needs the
    window in order to fix the settings.
    """
    path = Path(path or CONFIG_PATH)
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppConfig()

    entities = [EntityConfig(**e) for e in raw.pop("entities", []) if isinstance(e, dict)]
    known = {f for f in AppConfig.__dataclass_fields__ if f != "entities"}
    config = AppConfig(**{k: v for k, v in raw.items() if k in known})
    if entities:
        config.entities = entities
    # Repairs a config written before normalisation existed, or hand-edited.
    return config.normalise()
