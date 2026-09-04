# Setting up on your machine

Fifteen minutes, no administrator rights needed.

---

## 1. Copy the folder

Copy the whole `AriabNFA` folder from the shared drive to somewhere on your own
machine — `C:\Users\<you>\AriabNFA` is fine.

Run it from your own disk, not from OneDrive. Running directly out of a synced
folder means every generated file and log gets synced back up, and two people
running it from the same folder will collide.

## 2. Run `setup.bat`

Double-click it. It installs Python for your user account if you don't have it,
creates a private environment, and installs what the app needs. Two to five
minutes. It is safe to run again if something fails.

## 3. Start it

Double-click **`run_web.bat`**. Your browser opens at `http://127.0.0.1:5000/`.

Leave the black console window open while you use it — closing it stops the app.

Right-click `run_web.bat` → *Send to* → *Desktop (create shortcut)* to make it
easy to find.

---

## 4. Enter the Ariba details

Click **Settings**. Ask whoever set this up for the values — they are the same
for everyone on the team.

| Box | What goes in it |
|---|---|
| Realm | e.g. `InfrabeatDSAPP-T` |
| Environment | `prod` |
| OAuth host | `https://api.au.cloud.ariba.com` — this is the Australia data centre |
| API host | `https://openapi.au.cloud.ariba.com` |
| Event API path | `/api/sourcing-event/v2` |
| Integration user | e.g. `INT00996` — **required**, Ariba refuses the request without it |
| Password adapter | `PasswordAdapter1` |
| Application key | from the Ariba Developer Portal |
| OAuth Client ID / Secret | from the same place |

Enter **hosts and the path only** — not a whole endpoint URL copied from a
browser. The *Will call* line under those boxes shows the URL your settings
produce; check it looks right before saving.

Then press **Test connection**. It fetches a token only, so a failure there
means the credentials or the OAuth host are wrong — nothing else.

Your credentials go into Windows Credential Manager on your own machine. They
are never written to a file and never leave your PC.

## 5. Point at the shared field mapping

In Settings, set **Shared field mapping** to the file on the shared drive.

This is what keeps the team in step. The mapping tells the app which Ariba
fields feed which parts of the NFA; when someone corrects one, everybody picks
it up. If the drive is unreachable the app quietly uses its own copy instead of
refusing to start.

---

## Using it

1. Enter an Ariba event ID and press **Fetch**. It takes a few seconds.
2. Check the details. **Blue-highlighted fields** are ones Ariba did not supply
   and need typing in.
3. Correct the vendor list if needed — untick anyone who did not bid.
4. Write the **Justification**. It is the only written section in the document.
5. Press **Generate NFA**, then **Download**.

Rows marked *auto* fill themselves from the vendor list. Edit the vendors, not
those rows, so the grid can never disagree with the comparison table.

Anything the tool could not fill prints in the document as a visible
`«placeholder»` rather than a blank cell — a blank in an approval document can
be signed off without anyone noticing.

To try it without touching Ariba, tick **Offline** and fetch `sample`,
`multiline` or `awarded`.

---

## If something goes wrong

| What you see | What it means |
|---|---|
| "The host … does not exist" | A full URL was pasted into *API host* or *Event API path*. Check the *Will call* line. |
| `400 The user parameter is missing` | *Integration user* is empty in Settings. |
| Test connection fails | Wrong Client ID/Secret, **or the wrong OAuth host** — a wrong data centre looks exactly like a wrong password. |
| Fetch works but vendor prices are blank | Nobody has bid on that event yet. Not a fault. |
| Nothing happens on double-click | Run `setup.bat` again and read the last few lines. |

Logs are in `%LOCALAPPDATA%\AriabNFA\logs\`. Credentials are scrubbed from them,
so they are safe to send on when asking for help.
