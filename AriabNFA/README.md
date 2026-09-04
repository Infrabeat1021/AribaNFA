# Ariba NFA Generator

Generates an **Award Note / Note For Approval** as a Word document from a SAP Ariba
sourcing event: fetch the event, review and correct the details, write one
justification, click Generate.

The document carries the corporate letterhead, the lettered details grid, a top-3
vendor comparison ranked L1/L2/L3, and your justification. Anything the tool
could not fill is printed as a visible `«placeholder»` rather than left blank —
a blank cell in an approval document can be signed off without anyone noticing.

---

## Setup (once)

1. Install Python 3.12 — no admin rights needed:

   ```
   winget install --id Python.Python.3.12 --scope user --source winget
   ```

2. Double-click **`setup.bat`**. It creates a virtual environment and installs
   everything.

3. Start it one of two ways — both drive the same engine:

   - **`run_web.bat`** — opens in your browser. Recommended.
   - **`run_nfa.bat`** — the desktop window.

   Right-click either → *Send to* → *Desktop (create shortcut)* for easy access.

> Use `.bat`, not `.ps1` — PowerShell script execution is disabled by policy on
> this machine, so a `.ps1` will not run on double-click.

---

## Using it

### Offline first

Tick **Offline (use sample data)** and press **Fetch**. This runs the entire
app — form, ranking, document generation — with no credentials and no network,
using the sample events in `tests/fixtures`. Type `sample` or `sparse` as the
event ID to load either fixture. Use this to check the document layout is right
before touching Ariba.

### With Ariba

1. **Settings…** → fill in the connection details and credentials (see
   *Connecting to Ariba* below).
2. **Test connection** — this fetches a token only, so it isolates a credential
   problem from an entitlement problem.
3. Enter an event ID, press **Fetch**.
4. Check the highlighted fields, correct the vendor list, write the
   justification, press **Generate NFA**.

Amber fields are ones Ariba did not supply — they need your input.

---

## Connecting to Ariba

Everything is entered in **Settings…**. Nothing needs to be edited in code.

### Where the values come from

Sign in to the **SAP Ariba Developer Portal** (<https://developer.ariba.com>),
open your application, and generate OAuth credentials. That gives you:

| Settings field | Ariba portal name | Example |
|---|---|---|
| Application key (API key) | *Application Key* / *API Key* | `k1Yb...9Xq` |
| OAuth Client ID | *OAuth Client ID* | `a1b2c3d4-...` |
| OAuth Client Secret | *OAuth Client Secret* | `Zx8...` |
| Realm | your site's realm name | `MYCOMPANY-T` (test), `MYCOMPANY` (prod) |

Enter the **Client ID and Secret** — the app Base64-encodes them into the OAuth
`Authorization: Basic` header for you. The *Base64 credential* box below them is
only for the case where the portal gave you nothing but that combined string; if
both are filled, the ID and Secret win.

If your application is not yet registered, or you are unsure about entitlements,
see [ARIBA_ACCESS_REQUEST.md](ARIBA_ACCESS_REQUEST.md) for the request to send
your Ariba administrator.

### Endpoints

| Setting | Default | Notes |
|---|---|---|
| OAuth host | `https://api.ariba.com` | **Data-centre specific.** US default; e.g. Australia is `https://api.au.cloud.ariba.com`. |
| API host | `https://openapi.ariba.com` | Where all data calls go; same regional pattern. |
| Event API path | `/api/sourcing-event/v2` | Service path only — **not** a full URL. |
| Environment | `sandbox` | Becomes the last path segment: `prod` or `sandbox`. |
| Integration user | *(blank)* | **Required.** Ariba answers 400 without it. |
| Password adapter | `PasswordAdapter1` | Usually this default. |

Enter **hosts and the path only**. Pasting a whole endpoint URL into either box
was the cause of a real failure: the missing leading slash glued `api` onto the
hostname and produced `openapi.au.cloud.ariba.comapi`, which does not exist. The
app now trims a pasted URL back to the right pieces — and harvests the `user` and
`passwordAdapter` from it — but the **Will call:** preview under the fields shows
the assembled URL so you can see exactly what will be requested.

Those settings combine into:

```
{API host}{Event API path}/{Environment}/events/{eventId}
    ?realm={Realm}&user={Integration user}&passwordAdapter={Password adapter}
```

Requests also carry an `apiKey` header and an `Authorization: Bearer` token; the
token is fetched and refreshed automatically.

**The integration user is easy to miss.** It is not the OAuth Client ID — it is a
named user in your Ariba realm with API access. Without it every call fails with
`400 "The user parameter is missing."`, regardless of how correct everything else
is.

### What each failure means

Working through these in order is what stops you debugging the wrong thing.

| Symptom | Cause |
|---|---|
| Test connection fails | Wrong Client ID/Secret, **or wrong OAuth host** — a wrong data centre returns a 401 identical to a bad password. |
| "The host … does not exist" | A full URL was pasted into *API host* or *Event API path*. Check the **Will call:** preview. |
| `400 The user parameter is missing` | *Integration user* is blank in Settings. |
| Test connection works, Fetch gives 403 | Credentials are right but the application is not entitled to this API or realm. That is an administrator request, not a local fix. |
| Fetch gives 404 for an event you can see in Ariba | Wrong *Event API path*, wrong *Environment* (sandbox vs prod), or wrong realm. |
| Vendors appear but with no prices | Bid data is not in the Event Management response. If the event has `hasAward: false` there is genuinely nothing to fetch yet — see the access-request document. |

### Pre-filling without the dialog

Non-secret settings live in `%APPDATA%\AriabNFA\config.json` and can be edited
directly or copied to a colleague:

```json
{
  "realm": "MYCOMPANY-T",
  "oauth_base": "https://api.ariba.com",
  "api_base": "https://openapi.ariba.com",
  "event_api_path": "/api/sourcing-event/v2",
  "api_env": "sandbox",
  "gst_rate": "18",
  "gst_inclusive": false
}
```

Credentials are **never** written there — they go to Windows Credential Manager.
For scripted or CI use they can instead come from the environment:
`ARIBA_CLIENT_ID`, `ARIBA_CLIENT_SECRET`, `ARIBA_API_KEY` (or `ARIBA_BASIC_B64`),
which override anything stored.

The **total cost** and **limited enquiry** rows are filled
automatically from the vendor list, so the grid can never disagree with the
comparison table. Edit the vendors, not those rows.

---

## Running it for a team

Each person runs their own copy — there is no server. The one thing that would
otherwise drift is the field mapping: someone corrects a path and nobody else
gets it.

Fix that by putting `nfa_mapping.json` on a shared drive and pointing every
install at it, in **Settings → Shared field mapping**. That gives you central
configuration with no hosting at all, which is the main thing a portal would
have bought you. If the drive is unreachable the app falls back to its local
copy rather than refusing to run, and says so in the log.

Credentials stay in each person's own Windows Credential Manager. Nothing is
shared but the mapping.

> **Do not bind the web interface to anything but loopback.** `--host 0.0.0.0`
> would turn a workstation into an unmanaged server holding Ariba credentials,
> with no TLS and no login. If several people need one shared instance, that
> wants a real host and a security review, not a desktop.

---

## Wiring up your realm's field names

Field names differ between Ariba realms, and custom fields are often keyed by an
internal ID rather than the label shown in the Ariba UI. So the tool never
assumes a shape: `mapping/nfa_mapping.json` lists **candidate paths per field,
tried in order, first non-empty wins**.

To find the real paths:

1. Enter a real event ID and press **Dump raw JSON…** (or run
   `dev.bat --dump YOUR_EVENT_ID`).
2. Open the generated `*_paths.csv` in Excel — it lists every field path with a
   **sample value** beside it. Match on the values; that is the only practical
   way to identify an opaque custom-field ID.
3. Paste the real path into `mapping/nfa_mapping.json`. **Append** it to the
   candidate list rather than replacing what is there, so both sandbox and
   production keep working.
4. Fetch again. No Python is edited to rewire a field.

Dumps go to `Documents\AriabNFA\dumps\`. They can contain supplier pricing, so
they are kept outside this folder and are gitignored.

---

## Where things live

| What | Where |
|---|---|
| Settings (no secrets) | `%APPDATA%\AriabNFA\config.json` |
| API key & client credential | Windows Credential Manager, under `AriabNFA` |
| Generated documents | the output folder set in Settings |
| Drafts (autosaved) | `%APPDATA%\AriabNFA\drafts\` |
| Logs | `%LOCALAPPDATA%\AriabNFA\logs\` |
| Raw dumps & field CSVs | `Documents\AriabNFA\dumps\` |

Credentials are never written to `config.json` and are scrubbed from the log
files — log files get emailed to support, so redaction is not optional.

---

## Two settings worth getting right

**GST inclusive or exclusive.** If Ariba quotes already include GST and the tool
assumes otherwise, every row of the comparison table is misstated while looking
entirely plausible. Confirm it in Settings against an event whose real figures
you know.

**The OAuth host is data-centre specific.** `api.ariba.com` is the US host. A
wrong host produces a 401 that is indistinguishable from a wrong password —
confirm yours with your Ariba administrator before spending a day debugging
credentials.

---

## Development

```
.venv\Scripts\python.exe -m pytest        # 109 tests, no network needed
dev.bat                                   # run with a console and debug logging
```

The architecture has one load-bearing seam: `mapping/extract.py` converts Ariba
JSON into a plain `NFAData` object. `docgen` never imports anything Ariba-related
and `ariba` never imports `python-docx`. That is what lets the whole document
pipeline be tested with no credentials.

`docgen/docx_helpers.py` holds the OOXML workarounds for things `python-docx`
has no API for: the double page border, `PAGE`/`NUMPAGES` fields, genuinely
fixed column widths, and repeating table header rows.

`tests/fixtures/event_sparse.json` is deliberately adversarial — renamed fields,
nulls instead of omissions, a partial bid, a tie at L1, a non-responder, and a
responder with an unreadable price. Graceful degradation and correct ranking are
the things most likely to break silently, so they have their own fixture.
