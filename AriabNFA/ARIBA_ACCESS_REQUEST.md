# What to ask the Ariba administrator for

Send this early. API entitlement is the longest-lead item in this project and the
only part you cannot unblock yourself — everything else already works offline.

---

## The request

> I am building an internal tool that generates our NFA (Award Note / Note For
> Approval) documents directly from Ariba sourcing events, to remove the manual
> re-keying of commercial figures into Word.
>
> Could you please arrange the following:
>
> **1. An application registered on the SAP Ariba Developer Portal**, with OAuth
> client credentials for our realm. I need:
> - the **Application Key** (API key)
> - the **Base64-encoded Client ID and Client Secret**
> - the **realm name**, for both the **test realm** (usually `-T` suffixed) and
>   production
>
> **2. Entitlement to the Event Management API** (`sourcing-event/v2`) — the
> `GET /events/{eventId}` and `GET /events/{eventId}/items` endpoints.
>
> **3. Access to per-vendor bid pricing.** This is the part I most need
> confirmation on. I need, for each vendor invited to an event: the vendor name,
> whether they submitted an offer, and their quoted value. If that is not
> exposed by the Event Management API on our realm, please advise whether we
> have — or can obtain — **Operational Reporting for Sourcing**
> (`sourcing-reporting-view/v1`), and whether a reporting **view template** needs
> to be created for it.
>
> **4. Confirmation of our data centre**, so I use the correct OAuth host. The
> default `https://api.ariba.com` is the US host, and a wrong host returns a 401
> that looks identical to a bad credential.
>
> **5. The rate limits** that apply to the application, per minute and per day.

---

## Why item 3 is the one that matters

The NFA contains a comparison of the top three participating vendors ranked
L1/L2/L3. Without per-vendor quoted values there is nothing to rank, so this is
the only requirement that genuinely blocks the finished document.

It is also the one most likely to need extra work on Ariba's side: bid data
frequently lives in Operational Reporting rather than Event Management, which
can mean a separate entitlement, an asynchronous request-then-poll pattern, and
a view template that only a realm administrator can create.

**The tool works without it in the meantime.** The vendor list is fully editable
in the review form, so vendor names and values can be typed in and everything
else — letterhead, the A–P grid, ranking, GST, amounts in words, the document
itself — is still produced automatically.

---

## What to check when the credentials arrive

Work through these in order, changing one thing at a time. Each step isolates a
different failure, which is what stops a wrong host being mistaken for a wrong
password.

1. **Settings → Test connection.** Fetches a token only. Proves the OAuth host
   and the Base64 credential.
2. **Fetch any event ID.** A 403 here rather than a 401 means the credentials are
   right but the application is not entitled — that is a reply to the
   administrator, not something to debug locally.
3. **Dump raw JSON** for an event whose correct NFA you already have on file.
   A known answer makes the field mapping quick to verify.
4. **Open the `*_paths.csv` in Excel** and check whether per-vendor pricing is
   actually present. This is the moment you find out whether item 3 landed.
5. **Confirm whether quoted values include GST**, and set that in Settings.
   Getting it backwards misstates every comparison row while still looking
   plausible.
6. **Re-verify against production** before go-live. Custom fields on a `-T`
   sandbox realm often differ from production.
