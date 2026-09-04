# Status — 26 Aug 2026

Working, connected to live Ariba, 200 tests passing.
Backup: `D:\OneDrive - infrabeat.com\Ariba DSN Configuration\AriabNFA-backup-*.zip`

---

## Start here tomorrow

Launch from the **Ariba NFA Generator** shortcut on the Desktop, or `run_nfa.bat`.

Settings are already saved and working — realm `InfrabeatDSAPP-T`, Australia data
centre, integration user `INT00996`. Nothing to re-enter.

### The one test that matters

**Fetch an event that has actually been awarded** (or at least has supplier bids).

Everything else is verified against live data. This single test resolves the last
open question: whether per-supplier line pricing comes through, and whether the
field names guessed inside `supplierBids` are right.

Three possible outcomes:

| What you see | What it means | Next step |
|---|---|---|
| Comparison and item-wise tables fill with real figures | Everything works | Nothing — start using it |
| Vendors listed, prices blank | The bid field names differ on your realm | Run `dev.bat --dump <eventId>`, open the `*_paths.csv` in Excel, find the price paths, paste into `mapping/nfa_mapping.json` under `supplier_bids`. No code change. |
| `403` on the scenarios call | Not entitled to that API | Send `ARIBA_ACCESS_REQUEST.md` to the Ariba admin |

---

## What works, verified against live Ariba

- OAuth against the Australia data centre; token refresh
- Event fetch, with `realm` + `user` + `passwordAdapter` on every call
- Type of Order, Indentor, PR number, Approved Budget, supplier names, commodity
- Letterhead reading **InfraBeat**, no logo
- Document generation — Word opens it with no repair prompt
- Field discovery dump (`--dump`) writing raw JSON plus a paths CSV

## What is built but not yet proven on real data

- **Per-supplier line pricing** from `GET /events/{id}/scenarios`. The endpoint is
  confirmed real and returns a `supplierBids` array; it was empty on every event
  tried, so the paths *inside* a bid are inferred. Flagged in the mapping file.
- **Item-wise Supplier Price Detail** section — fully tested against a fixture
  shaped like the real API, appears only when an event has 2+ priced lines.
- **Department → row B.** `departments` was `[]` on the test event, so the shape is
  inferred from its sibling `commodities`.

## Known blanks that are not bugs

Rows B, E, G, I, J, K, M, N come back empty because this realm returns custom
fields as *definitions without values*. They are typed into the review form.
`Doc90436602` has `hasAward: false` and no bids, so it can never show prices.

---

## Things worth doing when convenient

- **Add the InfraBeat logo and address.** Drop an image in `assets/letterheads/`,
  then set `logo` and `address` in `%APPDATA%\AriabNFA\config.json`. The letterhead
  switches to logo-left / name-right automatically.
- **Confirm GST inclusive vs exclusive** against an event whose real figures you
  know. Currently set to exclusive. Wrong either way misstates every comparison row
  while still looking plausible.
- **Consider `regions[*].name` for row C** instead of commodity, if production
  events populate it — closer to the original NFA's "Equipment / Area" meaning.
  One-line reorder in the mapping file.
- **No version control on this machine.** `winget install --id Git.Git --scope user`
  would fix that; until then the OneDrive zip is the only history.
