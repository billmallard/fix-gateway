#  SPDX-License-Identifier: GPL-2.0-or-later
#
# Garmin .fpl flight-plan exchange with an EFB over the panel's Wi-Fi
# (AER-6321, BACKLOG EFB4).
#
# The design, the tap counts and what a plan loses on the way are in
# doc/plugins/fpl_exchange.md. In short: a small HTTP server that offers the
# loaded plan as a .fpl download and takes a .fpl upload. An upload becomes a
# pending plan on the FPLIMP* keys, which this plugin alone writes; it never
# writes a route slot, FPLSEQ or FPLCMD. The pilot accepts the pending plan
# on the FPL page, and the display commits it the normal way (EFB4b).
"""fixgw.plugins.fpl_exchange: .fpl download and upload over HTTP."""

import email.parser
import email.policy
import html
import ipaddress
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fixgw.plugin as plugin
from fixgw.plugins.fpl_exchange import fpl

PORT = 8080
#: Seconds a connection may sit idle on one read before it is dropped.
REQUEST_TIMEOUT_S = 10.0
#: Connections served at once; the next is closed unanswered.
MAX_CONNECTIONS = 4
#: The largest upload max_upload_bytes may be set to.
UPLOAD_CEILING = 1048576
#: Multipart framing around the file, on top of max_upload_bytes.
FORM_OVERHEAD = 4096

PENDING_FIELDS = ("ID", "LAT", "LON", "TYPE")


def parse_allow(value, log):
    """The networks a client may connect from, or None for any.

    An entry that does not parse must not open the filter: the result then
    matches nobody, and the error is logged (the stratux plugin's ``source``
    rule, as in gdl90_out's ``discovery_allow``)."""
    if value is None or value == "" or value == []:
        return None
    entries = value if isinstance(value, list) else [value]
    nets = []
    for entry in entries:
        try:
            nets.append(ipaddress.IPv4Network(str(entry).strip(), strict=False))
        except ValueError:
            log.error("allow: %r is not an IPv4 network; refusing every client" % (entry,))
            return []
    return nets


def allowed(nets, address):
    if nets is None:
        return True
    try:
        addr = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return any(addr in n for n in nets)


class Exchange:
    """The bus side: the loaded plan out, an uploaded plan in as pending.

    ``read(key)`` returns a FIX value tuple and ``write(key, value)`` writes
    one; both come from the plugin, so every write is this plugin's."""

    def __init__(self, read, write, log, max_upload_bytes):
        self.read = read
        self.write = write
        self.log = log
        self.max_upload_bytes = max_upload_bytes
        self._lock = threading.Lock()
        self.accepted = 0
        self.refused = 0
        self.last = "none"

    def _value(self, key):
        return self.read(key)[0]

    # --- out ---------------------------------------------------------------
    def loaded_plan(self):
        """The plan the engine has loaded, as an fpl.Plan, or (None, reason).

        Only an accepted commit is offered: when FPLLDSEQ differs from FPLSEQ
        the slots hold a route the engine refused (flightplan_keys.md)."""
        count = int(self._value("FPLCOUNT"))
        seq = int(self._value("FPLSEQ"))
        if count == 0 or seq == 0:
            return None, "No flight plan is loaded."
        if int(self._value("FPLLDSEQ")) != seq:
            return None, "The flight plan on the bus was refused by the engine."
        points = []
        for i in range(1, count + 1):
            ident = fpl.clean_ident(self._value("FPL%dID" % i))
            if not ident:
                continue   # a leg with no fix of its own (CA, VA, ...)
            p = fpl.Point(
                ident=ident,
                type=fpl.FIX_TO_TYPE.get(int(self._value("FPL%dTYPE" % i)), "USER WAYPOINT"),
                lat=float(self._value("FPL%dLAT" % i)),
                lon=float(self._value("FPL%dLON" % i)),
            )
            last = points[-1] if points else None
            if last and fpl.same_place(last, p):
                continue   # a hold at the fix, or one fix from two sources
            points.append(p)
        if not points:
            return None, "The loaded flight plan has no fixes."
        name = self._value("FPLNAME") or "%s %s" % (points[0].ident, points[-1].ident)
        return fpl.Plan(name=name, points=points), None

    def export(self):
        """(filename, bytes) for the loaded plan, or (None, reason)."""
        plan, why = self.loaded_plan()
        if plan is None:
            return None, why
        try:
            data = fpl.write(plan)
        except fpl.FplError as e:
            return None, "The plan cannot be written as .fpl: %s." % e.reason
        stem = fpl.clean_name(plan.name).replace(" ", "_").replace("/", "_") or "PANEL"
        return stem + ".fpl", data

    # --- in ----------------------------------------------------------------
    def pending(self):
        seq = int(self._value("FPLIMPSEQ"))
        return seq > 0 and int(self._value("FPLIMPDONE")) != seq

    def receive(self, data, source):
        """Parse an upload and post it as the pending plan. Returns
        (True, plan) or (False, reason); a refusal leaves the bus as it was."""
        try:
            plan = fpl.parse(data, max_bytes=self.max_upload_bytes)
        except fpl.FplError as e:
            self.refused += 1
            self.last = "refused from %s: %s" % (source, e.reason)
            self.log.warning("fpl_exchange: refused an upload from %s: %s" % (source, e.reason))
            return False, e.reason
        with self._lock:
            # Every slot, then the count and the name, then the sequence
            # last: a display acts on FPLIMPSEQ, as on FPLSEQ, so the block
            # is complete when it changes.
            for i, p in enumerate(plan.points, start=1):
                self.write("FPLIMP%dID" % i, p.ident)
                self.write("FPLIMP%dLAT" % i, p.lat)
                self.write("FPLIMP%dLON" % i, p.lon)
                self.write("FPLIMP%dTYPE" % i, p.fix_type)
            self.write("FPLIMPCOUNT", len(plan.points))
            self.write("FPLIMPNAME", plan.name)
            seq = max(int(self._value("FPLIMPSEQ")), int(self._value("FPLIMPDONE"))) + 1
            self.write("FPLIMPSEQ", seq)
        self.accepted += 1
        self.last = "pending %d from %s: %s, %d points" % (seq, source, plan.name or "(no name)", len(plan.points))
        self.log.info("fpl_exchange: " + self.last)
        return True, plan


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel flight plan</title>
<style>body{{font-family:-apple-system,sans-serif;margin:1.2em;font-size:1.1em}}
a.b,label.b{{display:block;padding:.9em;margin:.6em 0;border:2px solid #246;border-radius:.4em;
text-align:center;text-decoration:none;color:#246;font-weight:600}}input{{display:none}}</style>
</head><body>
<h2>Panel flight plan</h2>
<p>Supplemental only. A plan sent to the panel is not flown until you accept it on the FPL page.</p>
{download}
{upload}
</body></html>
"""

UPLOAD_FORM = """<form method="post" action="/upload" enctype="multipart/form-data" id="f">
<label class="b">Send a .fpl file to the panel
<input type="file" name="fpl" accept=".fpl" onchange="document.getElementById('f').submit()">
</label></form>"""


def _page(exchange, download_on, upload_on):
    if download_on:
        plan, why = exchange.loaded_plan()
        if plan is None:
            down = "<p>%s</p>" % html.escape(why)
        else:
            route = " ".join(p.ident for p in plan.points)
            down = '<p>Loaded: %s</p><a class="b" href="/plan.fpl">Open in ForeFlight (download .fpl)</a>' % (
                html.escape(route))
    else:
        down = ""
    up = UPLOAD_FORM if upload_on else ""
    return PAGE.format(download=down, upload=up).encode("utf-8")


def make_handler(exchange, download_on, upload_on, max_upload_bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fixgw-fpl"
        sys_version = ""
        protocol_version = "HTTP/1.0"
        timeout = REQUEST_TIMEOUT_S

        def log_message(self, format, *args):
            exchange.log.debug("fpl_exchange: %s %s" % (self.client_address[0], format % args))

        def _reply(self, code, body, ctype="text/plain; charset=utf-8", extra=()):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._reply(200, _page(exchange, download_on, upload_on), "text/html; charset=utf-8")
            if path == "/plan.fpl" and download_on:
                name, data = exchange.export()
                if name is None:
                    return self._reply(404, data + "\n")
                return self._reply(200, data, "application/xml", [
                    ("Content-Disposition", 'attachment; filename="%s"' % name)])
            return self._reply(404, "Not found.\n")

        do_HEAD = do_GET

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/upload" or not upload_on:
                return self._reply(404, "Not found.\n")
            if self.headers.get("Transfer-Encoding"):
                return self._reply(411, "A Content-Length is required.\n")
            ctype = self.headers.get("Content-Type", "")
            form = ctype.lower().startswith("multipart/form-data")
            limit = max_upload_bytes + (FORM_OVERHEAD if form else 0)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411, "A Content-Length is required.\n")
            if length < 0:
                return self._reply(400, "Bad Content-Length.\n")
            if length > limit:
                exchange.refused += 1
                exchange.last = "refused from %s: TOO LARGE (%d bytes)" % (self.client_address[0], length)
                exchange.log.warning("fpl_exchange: " + exchange.last)
                return self._reply(413, "Too large: the limit is %d bytes.\n" % max_upload_bytes)
            body = self.rfile.read(length)
            if len(body) != length:
                return self._reply(400, "The upload was cut short.\n")
            if form:
                data = _form_file(ctype, body)
                if data is None:
                    return self._reply(400, "Send one .fpl file in the form field 'fpl'.\n")
            else:
                data = body
            ok, result = exchange.receive(data, self.client_address[0])
            if not ok:
                return self._reply(413 if result == "TOO LARGE" else 400, "Not accepted: %s.\n" % result)
            text = "Sent to the panel: %s, %d waypoints. Accept it on the FPL page.\n" % (
                result.name or "(no name)", len(result.points))
            if form:
                return self._reply(200, "<!DOCTYPE html><meta name=viewport content='width=device-width'>"
                                        "<p>%s</p><p><a href='/'>Back</a></p>" % html.escape(text),
                                   "text/html; charset=utf-8")
            return self._reply(200, text)

    return Handler


def _form_file(ctype, body):
    """The one file in a multipart/form-data body, field ``fpl``, or None."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        return None
    parts = [p for p in msg.iter_parts()]
    files = [p for p in parts if p.get_param("name", header="content-disposition") == "fpl"]
    if len(parts) != 1 or len(files) != 1:
        return None
    data = files[0].get_payload(decode=True)
    return data if isinstance(data, bytes) else None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, allow, log):
        self.allow = allow
        self.log = log
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.dropped = 0
        super().__init__(address, handler)

    def verify_request(self, request, client_address):
        if not allowed(self.allow, client_address[0]):
            self.dropped += 1
            return False
        if not self.slots.acquire(blocking=False):
            self.dropped += 1
            return False
        return True

    def handle_error(self, request, client_address):
        # socketserver prints a traceback to stderr; a client that hangs up
        # mid-reply is routine on Wi-Fi.
        e = sys.exc_info()[1]
        if isinstance(e, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            self.log.debug("fpl_exchange: %s: %s" % (client_address[0], e))
        else:
            self.log.warning("fpl_exchange: request from %s failed: %r" % (client_address[0], e))

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
        self.status = OrderedDict()
        size = int(config.get("max_upload_bytes", fpl.MAX_BYTES))
        self.max_upload_bytes = max(1, min(size, UPLOAD_CEILING))
        self.download_on = bool(config.get("download", True))
        self.upload_on = bool(config.get("upload", True))
        self.allow = parse_allow(config.get("allow"), self.log)
        self.address = (config.get("host", ""), int(config.get("port", PORT)))
        self.exchange = Exchange(self.db_read, self.db_write, self.log, self.max_upload_bytes)
        self.missing = self._missing_keys() if self.upload_on else []
        if self.missing:
            # A config tree whose database/flightplan.yaml predates EFB4
            # keeps it across updates: no pending plan can be posted.
            self.log.error("fpl_exchange: the database has no %s (database/flightplan.yaml predates "
                           "EFB4); uploads are refused" % self.missing[0])
            self.upload_on = False
        self.server = None
        self.thread = None

    def _missing_keys(self):
        keys = set(self.db_list())
        need = ["FPLIMPCOUNT", "FPLIMPNAME", "FPLIMPSEQ", "FPLIMPDONE"]
        need += ["FPLIMP%d%s" % (fpl.MAX_POINTS, f) for f in PENDING_FIELDS]
        return [k for k in need if k not in keys]

    def run(self):
        handler = make_handler(self.exchange, self.download_on, self.upload_on, self.max_upload_bytes)
        self.server = Server(self.address, handler, self.allow, self.log)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.5},
                                       name="fpl_exchange", daemon=True)
        self.thread.start()
        self.started = time.time()

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(2.0)
            if self.thread.is_alive():
                raise plugin.PluginFail

    def get_status(self):
        self.status["Listening"] = "%s:%d" % (self.address[0] or "*", self.address[1]) if self.server else "no"
        self.status["Download"] = "on" if self.download_on else "off"
        self.status["Upload"] = "on" if self.upload_on else ("off: database predates EFB4" if self.missing else "off")
        self.status["Pending plan"] = "yes" if self.upload_on and self.exchange.pending() else "no"
        self.status["Uploads accepted"] = self.exchange.accepted
        self.status["Uploads refused"] = self.exchange.refused
        self.status["Connections dropped"] = self.server.dropped if self.server else 0
        self.status["Last"] = self.exchange.last
        return self.status
