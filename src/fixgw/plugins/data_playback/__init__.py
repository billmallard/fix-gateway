import threading
import time
import fixgw.plugin as plugin
import fixgw.database as database
from collections import OrderedDict
import json
import datetime


def replay(key, recorded):
    """Write one recorded key back into the database.

    data_recorder writes each key in db_read() order:
    [value, annunciate, old, bad, fail, secfail]. The db_item value setter
    takes (value, annunciate, bad, fail, secfail) and has no old slot, because
    old is derived from the item's age against its tol when it is read.

    A recorded old is replayed the way it arose live: the item's timestamp is
    moved back past its tol, so the database itself derives old. When only old
    changed, the value setter is skipped so that consumers do not see a fresh
    sample before the stale one.
    """
    if len(recorded) != 6:
        raise ValueError(
            f"Playback row for {key} has {len(recorded)} fields, expected 6"
        )
    value, annunciate, old, bad, fail, secfail = recorded
    item = database.get_raw_item(key)
    current = item.value
    stale_only = (
        old
        and current[0] == value
        and [bool(f) for f in current[1:2] + current[3:]]
        == [bool(f) for f in (annunciate, bad, fail, secfail)]
    )
    if not stale_only:
        item.value = (value, annunciate, bad, fail, secfail)
    if bool(old) != item.value[2]:
        if old and item.tol:
            item.timestamp = time.time() - (item.tol + 1) / 1000
        item.old = bool(old)


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        self.getout = False  # indicator for when to stop
        self.parent = parent  # parent plugin object
        self.log = parent.log  # simplifies logging
        self.config = parent.config

        self.starttime = time.monotonic()

    def run(self):
        while not self.getout:
            freq = 500
            start_time_found = True
            start_time = None
            if self.config.get("start_time", False):
                self.log.debug("Looking for start time")
                start_time_found = False
                start_time = self.config["start_time"]
            self.log.debug(f"start_time_found: {start_time_found}")
            for file in self.config["files"]:
                path = file.format(CONFIG=self.config["CONFIGPATH"])
                file_time = None
                with open(path) as f:
                    for line in f:
                        j = json.loads(line)
                        if j.get("frequency", False):
                            freq = int(j["frequency"])
                            file_time = datetime.datetime.fromisoformat(
                                j["starttime"]
                            ) + datetime.timedelta(milliseconds=freq)
                            self.log.debug(f"{freq} {file_time} {start_time_found}")
                            continue
                        if not start_time_found:
                            self.log.debug("Looking for start time")
                            if file_time >= start_time:
                                # We found the start time we were looking for
                                start_time_found = True
                                self.log.debug("Found Start Time")
                            else:
                                # keep looking for the start time
                                file_time += datetime.timedelta(milliseconds=freq)
                                self.log.debug(f"{file_time}  --  {start_time}")
                                continue
                        for key in j:
                            replay(key, j[key])
                        # Wait for remainder of frequency interval
                        time.sleep(
                            (freq / 1000)
                            - ((time.monotonic() - self.starttime) % (freq / 1000))
                        )
            # If we started with cli option for playback
            # Exit after we have processed all files
            if self.config.get("start_time", False):
                # When we do have a file matching the cli option start time
                # But that file is not long enough to contain the start time desired
                # If we get here and exit too quickly before everything is fully started
                # The gateway will lock up instead of exit
                # So we wait a few seconds before quitting
                time.sleep(5)
                self.getout = True
                self.parent.quit()

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.thread = MainThread(self)
        self.status = OrderedDict()

    def run(self):

        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return self.status
