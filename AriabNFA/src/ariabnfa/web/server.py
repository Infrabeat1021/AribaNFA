"""Flask routes for the local browser interface."""

from __future__ import annotations

import logging
import secrets
import threading
import webbrowser
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ..ariba.discovery import dump_event
from ..config import AppConfig, load_config, normalise_api_path, normalise_host
from ..errors import NFAError
from ..mapping.extract import assemble_nfa
from ..mapping.formatting import plain_amount, to_decimal
from ..model import GRID_FIELDS, NFAData, VendorQuote
from ..pipeline import fetch_nfa, generate_document, make_source
from ..secrets_store import (
    KEY_API_KEY,
    KEY_BASIC,
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    SecretsStore,
)

log = logging.getLogger(__name__)

#: Spare blank vendor rows offered on the review form.
SPARE_VENDOR_ROWS = 2
#: Fetched events held in memory. Bounded, because a long-running server that
#: never forgets would grow without limit.
MAX_SESSIONS = 24


class SessionStore:
    """Fetched events, keyed by a token that lives in the URL.

    In memory only: restarting the server loses them and the user re-fetches.
    That is the right trade for a local tool - persisting fetched events would
    mean writing supplier pricing to disk for no real gain.
    """

    def __init__(self, limit: int = MAX_SESSIONS):
        self._items: OrderedDict[str, NFAData] = OrderedDict()
        self._paths: dict[str, Path] = {}
        self._lock = threading.Lock()
        self.limit = limit

    def put(self, data: NFAData) -> str:
        token = secrets.token_urlsafe(12)
        with self._lock:
            self._items[token] = data
            while len(self._items) > self.limit:
                oldest, _ = self._items.popitem(last=False)
                self._paths.pop(oldest, None)
        return token

    def get(self, token: str) -> NFAData | None:
        with self._lock:
            return self._items.get(token)

    def replace(self, token: str, data: NFAData) -> None:
        with self._lock:
            if token in self._items:
                self._items[token] = data

    def set_document(self, token: str, path: Path) -> None:
        with self._lock:
            self._paths[token] = path

    def document(self, token: str) -> Path | None:
        with self._lock:
            return self._paths.get(token)


def create_app(config: AppConfig | None = None,
               secrets_store: SecretsStore | None = None) -> Flask:
    app = Flask(__name__)
    app.config["ARIBA"] = config or load_config()
    app.config["SECRETS"] = secrets_store or SecretsStore()
    app.config["SESSIONS"] = SessionStore()

    _register_routes(app)
    return app


def _cfg(app) -> AppConfig:
    return app.config["ARIBA"]


def _register_routes(app: Flask) -> None:

    @app.context_processor
    def _globals():
        cfg = _cfg(app)
        return {
            "cfg": cfg,
            "connected": app.config["SECRETS"].has_all(),
            "entities": cfg.entity_names(),
        }

    # ------------------------------------------------------------------ #
    # Fetch
    # ------------------------------------------------------------------ #

    @app.get("/")
    def index():
        return render_template("index.html", error=request.args.get("error"))

    @app.post("/fetch")
    def fetch():
        cfg = _cfg(app)
        event_id = (request.form.get("event_id") or "").strip()
        offline = request.form.get("offline") == "on"
        entity_name = request.form.get("entity") or None

        if not event_id and not offline:
            return redirect(url_for("index", error="Enter an Ariba event ID first."))

        cfg.api_env = request.form.get("api_env") or cfg.api_env
        try:
            source = make_source(cfg, offline=offline)
            data = fetch_nfa(source, event_id, config=cfg,
                             entity=cfg.entity(entity_name))
        except NFAError as exc:
            return redirect(url_for("index", error=exc.user_message))
        except Exception as exc:                       # noqa: BLE001
            log.exception("Fetch failed")
            return redirect(url_for("index", error=f"Unexpected error: {exc}"))

        if not data.event_id:
            data.event_id = event_id
        token = app.config["SESSIONS"].put(data)
        return redirect(url_for("review", token=token))

    # ------------------------------------------------------------------ #
    # Review and generate
    # ------------------------------------------------------------------ #

    @app.get("/review/<token>")
    def review(token: str):
        data = app.config["SESSIONS"].get(token)
        if data is None:
            return redirect(url_for("index", error="That fetch has expired. Fetch again."))
        return render_template(
            "review.html",
            token=token,
            data=data,
            rows=_grid_rows(data),
            vendors=_vendor_rows(data),
            document=app.config["SESSIONS"].document(token),
            error=request.args.get("error"),
        )

    @app.post("/review/<token>/generate")
    def generate(token: str):
        sessions = app.config["SESSIONS"]
        previous = sessions.get(token)
        if previous is None:
            return redirect(url_for("index", error="That fetch has expired. Fetch again."))

        cfg = _cfg(app)
        try:
            data = assemble_nfa(
                entity=cfg.entity(request.form.get("entity")),
                subject=(request.form.get("subject") or "").strip(),
                grid_values={
                    key: (request.form.get(f"grid__{key}") or "").strip()
                    for _letter, key, _label in GRID_FIELDS
                },
                all_vendors=_vendors_from_form(request.form),
                # Neither is editable on the form, so both must be carried
                # across or the item tables silently vanish from the document.
                line_items=previous.line_items,
                awarded_total=previous.awarded_total,
                justification=(request.form.get("justification") or "").strip(),
                doc_date=previous.doc_date,
                config=cfg,
                event_id=previous.event_id,
            )
            path = generate_document(data, config=cfg)
        except NFAError as exc:
            return redirect(url_for("review", token=token, error=exc.user_message))
        except Exception as exc:                       # noqa: BLE001
            log.exception("Generate failed")
            return redirect(url_for("review", token=token,
                                    error=f"Unexpected error: {exc}"))

        sessions.replace(token, data)
        sessions.set_document(token, path)
        return redirect(url_for("review", token=token))

    @app.get("/review/<token>/download")
    def download(token: str):
        path = app.config["SESSIONS"].document(token)
        if path is None or not Path(path).exists():
            abort(404)
        return send_file(path, as_attachment=True, download_name=Path(path).name)

    # ------------------------------------------------------------------ #
    # Diagnostics and settings
    # ------------------------------------------------------------------ #

    @app.post("/dump")
    def dump():
        cfg = _cfg(app)
        event_id = (request.form.get("event_id") or "").strip()
        offline = request.form.get("offline") == "on"
        try:
            result = dump_event(make_source(cfg, offline=offline), event_id or "sample")
        except NFAError as exc:
            return redirect(url_for("index", error=exc.user_message))
        return redirect(url_for(
            "index",
            error=f"Dumped {result.row_count} field paths to {result.csv_path}",
        ))

    @app.get("/settings")
    def settings():
        store = app.config["SECRETS"]
        return render_template(
            "settings.html",
            saved=request.args.get("saved"),
            tested=request.args.get("tested"),
            api_key=store.get(KEY_API_KEY) or "",
            client_id=store.get(KEY_CLIENT_ID) or "",
            client_secret=store.get(KEY_CLIENT_SECRET) or "",
            basic=store.get(KEY_BASIC) or "",
        )

    @app.post("/settings")
    def save_settings():
        cfg, store = _cfg(app), app.config["SECRETS"]
        form = request.form

        cfg.realm = (form.get("realm") or "").strip()
        cfg.oauth_base = normalise_host(form.get("oauth_base") or "")
        cfg.api_base = normalise_host(form.get("api_base") or "")
        cfg.event_api_path = normalise_api_path(form.get("event_api_path") or "")
        cfg.api_env = (form.get("api_env") or "sandbox").strip()
        cfg.api_user = (form.get("api_user") or "").strip()
        cfg.password_adapter = (form.get("password_adapter") or "").strip()
        cfg.gst_rate = (form.get("gst_rate") or "18").strip()
        cfg.gst_inclusive = form.get("gst_inclusive") == "on"
        cfg.output_dir = (form.get("output_dir") or cfg.output_dir).strip()
        cfg.mapping_file = (form.get("mapping_file") or "").strip()
        cfg.normalise()

        for key, name in ((KEY_API_KEY, "api_key"), (KEY_CLIENT_ID, "client_id"),
                          (KEY_CLIENT_SECRET, "client_secret"), (KEY_BASIC, "basic")):
            store.set(key, form.get(name) or "")

        try:
            cfg.save()
        except OSError as exc:
            return redirect(url_for("settings", saved=f"Could not save: {exc}"))

        if form.get("action") == "test":
            return redirect(url_for("settings", tested=_test_connection(cfg, store)))
        return redirect(url_for("settings", saved="Settings saved."))


def _test_connection(cfg: AppConfig, store: SecretsStore) -> str:
    try:
        from ..ariba.client import AribaClient

        return AribaClient(cfg, store).test_connection()
    except NFAError as exc:
        return exc.user_message
    except Exception as exc:                           # noqa: BLE001
        log.exception("Test connection failed")
        return f"Unexpected error: {exc}"


# --------------------------------------------------------------------------- #
# Form <-> model
# --------------------------------------------------------------------------- #

def _grid_rows(data: NFAData) -> list[dict]:
    values = data.grid_values()
    unresolved = set(data.report.unresolved)
    derived = {"total_cost", "limited_enquiry"}
    rows = []
    for letter, key, label in GRID_FIELDS:
        value = values.get(key, "")
        rows.append({
            "letter": letter, "key": key, "label": label, "value": value,
            "missing": not value.strip() or key in unresolved,
            "derived": key in derived,
            "multiline": "\n" in value,
        })
    return rows


def _vendor_rows(data: NFAData) -> list[dict]:
    ranked = {v.name: v for v in data.vendors}
    rows = []
    for vendor in (data.all_vendors or data.vendors):
        shown = ranked.get(vendor.name, vendor)
        rows.append({
            "name": vendor.name,
            "basic": plain_amount(vendor.basic) if vendor.basic is not None else "",
            "responded": vendor.responded,
            "gstin": vendor.gstin or "",
            "invitation_id": vendor.invitation_id or "",
            "partial": vendor.partial_bid,
            "rank": shown.rank_label if shown.rank else "",
            "total": plain_amount(shown.total) if shown.total is not None else "",
        })
    rows.extend([{"name": "", "basic": "", "responded": True, "gstin": "",
                  "invitation_id": "", "partial": False, "rank": "", "total": ""}
                 for _ in range(SPARE_VENDOR_ROWS)])
    return rows


def _vendors_from_form(form) -> list[VendorQuote]:
    names = form.getlist("vendor_name")
    basics = form.getlist("vendor_basic")
    gstins = form.getlist("vendor_gstin")
    invitations = form.getlist("vendor_invitation")
    partials = form.getlist("vendor_partial")
    submitted = set(form.getlist("vendor_submitted"))

    vendors: list[VendorQuote] = []
    for index, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        vendors.append(VendorQuote(
            name=name,
            basic=to_decimal(basics[index]) if index < len(basics) else None,
            gstin=(gstins[index] or None) if index < len(gstins) else None,
            invitation_id=(invitations[index] or None) if index < len(invitations) else None,
            responded=str(index) in submitted,
            partial_bid=(partials[index] == "1") if index < len(partials) else False,
        ))
    return vendors


# --------------------------------------------------------------------------- #

def find_free_port(host: str, preferred: int, attempts: int = 20) -> int:
    """The preferred port, or the next free one.

    A previous run that did not shut down cleanly leaves the port held, and
    Flask's failure to bind closes the console window instantly - which looks
    from the outside like the launcher doing nothing at all.
    """
    import socket

    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # No SO_REUSEADDR here. On Windows it permits binding to a port
            # another socket is actively listening on, so the probe would call
            # a busy port free and Flask would fail to bind anyway - exactly the
            # silent failure this function exists to prevent.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise OSError(
        f"No free port between {preferred} and {preferred + attempts - 1}. "
        "Close whatever is using them, or pass --port."
    )


def run(host: str = "127.0.0.1", port: int = 5000, *, open_browser: bool = True,
        config: AppConfig | None = None) -> None:
    chosen = find_free_port(host, port)
    if chosen != port:
        print(f"Port {port} is already in use - using {chosen} instead.")
        log.info("Port %s busy; serving on %s", port, chosen)

    app = create_app(config)
    url = f"http://{host}:{chosen}/"

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    log.info("Serving the NFA generator at %s", url)
    print()
    print("=" * 52)
    print("  InfraBeat NFA Generator is running")
    print("=" * 52)
    print()
    print(f"  Open this in your browser:  {url}")
    print()
    print("  Keep this window open while you use the app.")
    print("  Press Ctrl+C here to stop it.")
    print()

    try:
        app.run(host=host, port=chosen, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nStopped.")
