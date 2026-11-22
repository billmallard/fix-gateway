# Flight-plan exchange plugin (`fpl_exchange`)

`fixgw.plugins.fpl_exchange` moves a flight plan between the panel and an EFB
app such as ForeFlight as a Garmin `.fpl` file, over the Wi-Fi the tablet is
already on for the GDL-90 feed (`gdl90_out`). It is the flight-plan half of
CAP-720 (BACKLOG EFB4, AER-6321). The route was chosen in
`makerplane/briefs/efb_flightplan_exchange.md` (maos-workspace), after a bench
test showed GDL-90 carries no flight plan in either direction (AER-6290).

It is **supplemental only.** A plan from the tablet is never flown until the
pilot accepts it on the FPL page, and the plugin ships off
(`FPL_EXCHANGE: false`).

The file format is Garmin's published FlightPlan v1 schema,
`https://www8.garmin.com/xmlschemas/FlightPlanv1.xsd`, and nothing else. No
ForeFlight or Garmin protocol is used beyond it. ForeFlight imports and
exports this format with no partnership; the brief, section 2.2, has the
sources.

## What the pilot does

You are on the ground at KSBA with `KSBA RZS GVO V27 AVOLS KSMX` entered on
the panel's FPL page, and you want it in ForeFlight.

- **Panel to ForeFlight.** On the iPad, tap the panel's home-screen icon (a
  Safari bookmark of `http://<panel>:8080/`, added once). The page lists the
  loaded route. Tap *Open in ForeFlight (download .fpl)*. Safari offers the
  file, and iOS hands it to ForeFlight, where it becomes the active route.
  About 3 to 5 taps. The exact count depends on how this iOS version's
  Safari offers a downloaded file, and is measured on the bench (below).
- **ForeFlight to panel, with a shortcut (set up once).** In ForeFlight, tap
  *Send To*, then *Share FPL file*, then *Send to Panel* in the share sheet.
  3 taps. The panel's FPL page then offers the plan to accept (pyEfis, EFB4b).
- **ForeFlight to panel, with no setup.** *Send To*, *Share FPL file*, *Save
  to Files*, *Save*; then the panel's icon, *Send a .fpl file*, and pick the
  file from *Recents*. About 8 taps. The page sends the file as soon as it is
  picked, with no separate upload button.

Either way, the plan arrives as **pending**. It does not replace the route
being flown. The FPL page shows it with a note that altitudes and speeds did
not come across, and the pilot accepts or rejects it there.

### The shortcut

*Send to Panel* is an Apple Shortcuts shortcut, which the pilot builds once in
the Shortcuts app:

1. *Receive* **Files** *from* **Share Sheet**.
2. *Get contents of* `http://<panel>:8080/upload`, method **POST**, request
   body **File**, the file being *Shortcut Input*.
3. *Show* *Contents of URL* (optional: the panel's answer, such as "Sent to
   the panel: KSBA TO KSMX, 7 waypoints").

Shortcuts is part of iOS, and the shortcut sends an ordinary HTTP POST. That
uses no ForeFlight interface beyond its share sheet. A Shortcuts *Form*
request body also works: the endpoint takes a multipart form as well as a raw
body. **Unverified until the bench run:** the exact action names in the
current Shortcuts app, and that ForeFlight's share sheet lists a shortcut
that accepts files.

### Why an upload page and not a WebDAV folder

The BACKLOG item asked for one or the other, chosen by tap count.

- A **WebDAV folder** would let the pilot *Save to Files* straight onto the
  panel. That is about 5 taps, with no browser step. But the iOS Files app's
  *Connect to Server* speaks SMB, not WebDAV, so the folder would need a
  third-party WebDAV app on the iPad. That is **unverified on the iOS version
  Bill runs**, and it is checked on the bench. SMB is not something
  fix-gateway can serve. It would mean Samba on the box, with its own
  accounts and exposure.
- The **upload page** needs nothing on the iPad but Safari, at about 8 taps.
  With the shortcut it is 3 taps, fewer than any folder.

So the page and the shortcut are built, and the folder is not.

## The pending plan on the bus

An accepted upload is written to its own key block, `FPLIMP*`
(`database/flightplan.yaml`; contract in `doc/flightplan_keys.md`,
"Pending import"). This plugin is its only writer, apart from `FPLIMPDONE`,
which the editor writes. The plugin never writes a route slot, `FPLCOUNT`,
`FPLSEQ` or `FPLCMD`. A test asserts its exact writer set.

| Key | Writer | Meaning |
|---|---|---|
| `FPLIMP1..100 ID` / `LAT` / `LON` / `TYPE` | fpl_exchange | Point ident (Garmin identifier, up to 12), position, type as `FPLfTYPE` |
| `FPLIMPCOUNT` | fpl_exchange | Points in use |
| `FPLIMPNAME` | fpl_exchange | Route name from the file |
| `FPLIMPSEQ` | fpl_exchange | Counter, written last; 0 = nothing received since start |
| `FPLIMPDONE` | editor | The `FPLIMPSEQ` the pilot accepted or rejected |

A plan is pending while `FPLIMPSEQ > 0` and `FPLIMPDONE` differs from it. The
editor acts on `FPLIMPSEQ` changing, as it does on `FPLSEQ`. On **Accept** it
writes the points into the route block, as for any edit, and commits with
`FPLSEQ`. On **Reject** it does not. In both cases it then writes
`FPLIMPDONE = FPLIMPSEQ`. A newer upload replaces an older pending one. The
pending plan is not persisted, so a gateway restart drops it, and the pilot
shares it again.

**Why not `FPLCMD`.** The BACKLOG item said the import would go "through the
engine's commit path (`FPLCMD`)". `FPLCMD` is the editor's command channel. It
has one writer, and its sequence numbers belong to one editor session, so a
second writer would break both. The engine's commit path is the route block
plus `FPLSEQ`, which the editor writes when the pilot accepts. So the import
reaches the engine through that commit, after the pilot accepts it. Until
then it is only on the bus, beside the route block.

## What crosses, and what is lost

FlightPlan v1 is a waypoint table and an ordered list of route points:
identifier, type, ICAO country code and position. Nothing else crosses.

**From ForeFlight to the panel**, the editor gets an ordered list of fixes,
each with an ident, a position and a type: airport, VOR, NDB, intersection
(`INT`, and `INT-VRP` as an intersection), or user waypoint. It does not get:

- **Altitudes, speeds and step climbs.** The format has no field for them.
  ForeFlight's own support article says so as well.
- **Airways and procedures as such.** At most they arrive as their fixes.
  `V27` becomes `GVO ... AVOLS`, with no airway segment, and a SID, STAR or
  approach becomes plain fixes with no path terminators. Which of these
  ForeFlight puts in an export is **unverified** until Bill's export fixtures
  are in (`tests/plugins/fpl_exchange/data/foreflight/`). An accepted import
  is a route of TF legs to fixes. A procedure is loaded on the panel from the
  panel's own database.
- **Country codes.** They are read and checked, but the route block has no
  field for them.
- **Comments, elevations and symbols.** These are checked against the schema
  and dropped.
- **Points beyond 100.** The schema allows 300 route points, but the route
  block holds 100, so a longer route is refused (`TOO MANY POINTS`), never cut
  short.

**From the panel to ForeFlight**, the plugin offers the plan the engine has
loaded: the route block when `FPLLDSEQ` equals `FPLSEQ`. A route the engine
refused is not offered. ForeFlight gets each slot's ident, position and type:

- A slot with no ident (a `CA` or vector leg with no fix of its own) is left
  out. A slot that repeats the fix before it is written once. That covers a
  hold at that fix, and one fix from two sources: on the bench, VTU ended
  the departure and began the airway, 0.1 m apart. The same ident and type
  within 0.1 nm count as one fix.
- Path terminators, holds, arcs, altitude and speed constraints, segments and
  procedure names are lost. The format has no place for them.
- Types map back one to one (airport, VOR, NDB, fix to `INT`, user). An
  unknown type, and a map point, go out as user waypoints.
- **The country code is empty.** The bus does not carry it. The schema
  allows it, and says it should be empty for user waypoints. Whether
  ForeFlight then matches `KSBA AIRPORT` to its own database, or makes a user
  waypoint of it, is **checked on the bench**.
- Idents are upper-cased and stripped to letters and digits (the schema's
  `[A-Z0-9]{1,12}`). The route name is cleaned to `[A-Z0-9 /]`, 25 characters.
- Two places more than 0.1 nm apart under one ident, type and country code
  cannot both be written, because the schema keys the waypoint table on those
  three. The download is then refused with the ident named.

## The endpoints

| Method and path | What it does |
|---|---|
| `GET /` | The page: the loaded route, the download link and the upload control |
| `GET /plan.fpl` | The loaded plan as `.fpl`, sent as an attachment named after the route; 404 when none is loaded |
| `POST /upload` | A `.fpl` as the raw body, or as the one file in a multipart form field `fpl`. 200 with a one-line answer, 400 with the reason, 411 with no `Content-Length`, 413 over the size limit |

Every other path is 404. Nothing is read from or written to disk: the
download is built from the bus, and an upload goes to the bus or nowhere.

## Network input (CERT14, CERT15)

An upload is an unauthenticated network input, so:

- **Off by default** in the shipped config. A test runs the real gateway from
  the shipped config and finds the port refused and `FPLIMP*` unwritten.
- **Source filter.** `allow:` lists the networks a client may connect from.
  Empty accepts any IPv4 client. An entry that does not parse accepts nobody,
  and the error is logged (the `stratux` plugin's `source` rule). A refused
  client's connection is closed without an answer. `host:` can bind the
  server to the Wi-Fi interface only.
- **Bounded size.** `Content-Length` is required, chunked bodies are refused,
  and a body over `max_upload_bytes` (128 KiB by default, 1 MiB at most) is
  refused before it is read. A multipart form gets 4 KiB of framing on top.
  At most 4 connections are served at once, and the next is closed; a
  connection idle for 10 s on one read is dropped.
- **Strict parse.** The reader (`fpl.py`) is written from the schema. It
  refuses a document type declaration, any entity and any processing
  instruction before anything else, and bounds depth, element count and text
  length. It then checks every element, order, count, pattern and range the
  schema states, plus the schema's key and keyref: a route point must name a
  waypoint in the table, and the table may not repeat one. It is tested
  against the schema itself, with lxml: each of 36 hand-made mutations is
  refused by both, and over random edits to a valid file, nothing the reader
  accepts fails the schema (3,000 edits in the suite; 192,000 in one
  offline run, with none). Beyond the schema, it refuses a file with no
  route, a route over 100 points, processing instructions, and an XML
  declaration other than version 1.0 in UTF-8.

What this does **not** protect against: anything that can reach the page can
read the loaded plan and post a pending one. That includes a web page the
iPad visits, through a cross-site form post. A pending plan is never flown
without the pilot's accept, and that is the safeguard. The plan is not a
secret on the cockpit's own network.

## Configuration

`connections/fpl_exchange.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `host` | `""` | Address to listen on; `""` is every interface |
| `port` | `8080` | TCP port |
| `allow` | `[]` | Client networks, e.g. `["192.168.10.0/24"]`; empty is any |
| `max_upload_bytes` | `131072` | Largest `.fpl` accepted, at most 1048576 |
| `download` | `true` | Serve the loaded plan |
| `upload` | `true` | Accept uploads |

A database whose `database/flightplan.yaml` predates EFB4 has no `FPLIMP*`
keys. An install that edited that file keeps the old one across updates.
The plugin then logs the error once, turns uploads off, and still serves
downloads. Its status shows `Upload: off: database predates EFB4`.

## Bench check (BACKLOG EFB4, definition of done 2)

On the Pi 5 hardware bench, with Bill's iPad on the bench Wi-Fi:

1. Turn the plugin on (`FPL_EXCHANGE: true` and the `FPL_EXCHANGE_CONFIG`
   include in `preferences.yaml.custom`), set `allow:` to the bench Wi-Fi
   network, restart fixgw, and check `ss -ltn` shows the port.
2. Commit a plan on the FPL page. On the iPad, open `http://<panel>:8080/`,
   add it to the home screen, and send the plan to ForeFlight. Count the taps
   and note what ForeFlight shows: airports and VORs as themselves, or as user
   waypoints.
3. In ForeFlight, enter a route with an airway, then *Send To > Share FPL file*.
   Save one copy to Files, which becomes the test fixture. Send it to the
   panel by the page, and by the shortcut. Count the taps each way.
4. Read `FPLIMPSEQ`, `FPLIMPCOUNT` and the points over netfix, and compare
   them with the route in ForeFlight.
5. Check from a client outside `allow:` that the connection closes
   unanswered. Then turn the plugin off and check the port is closed.

## Sources

- Garmin FlightPlan v1 schema:
  https://www8.garmin.com/xmlschemas/FlightPlanv1.xsd (fetched 2026-11-22,
  sha256 `40e94cfa...f83f35e`, kept in `tests/plugins/fpl_exchange/data/`).
- The route decision: `makerplane/briefs/efb_flightplan_exchange.md`
  (maos-workspace), sections 0 and 2.2.
