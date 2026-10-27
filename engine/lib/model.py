"""Result store: every source emits dated observations of profile fields."""
import re
import threading
from collections import defaultdict

from .net import log
from .parsers import SN

FIELDS = ("screen_name", "name", "description", "location", "website", "avatar", "banner",
          "followers", "following", "tweets", "likes", "created_at", "pinned_tweet_id", "verified", "protected")


class Results:
    def __init__(self, query):
        self.query = query
        self.lock = threading.RLock()
        self.user_id = None
        self.current = None              # current handle
        self.live = {}                   # live profile facts
        self.obs = []                    # observations
        self.memorylol = {}              # handle -> [first, last] | None
        self.undated_handles = set()
        self.notes, self.failed, self.worked = [], [], set()
        self.foreign = defaultdict(set)  # handle -> {(user_id, date, evidence)}
        self.archive_ph = {}
        self.id_pages = []
        self.handle_scans = {}
        self.status_scans = {}
        self.related = {}
        self.about = None
        self.raw = {}

    # -------------------------------------------------- bookkeeping
    def ok(self, name):
        with self.lock:
            self.worked.add(name)

    def fail(self, name, err):
        e = str(err)
        if "Read timed out" in e:
            e = "timed out (Wayback/remote server slow)"
        elif "too many 429" in e:
            e = "HTTP 429 rate-limited / blocked"
        elif "Max retries exceeded" in e or "Connection" in e:
            e = "connection refused/reset (rate-limited?)"
        msg = f"{name}: {e}"
        with self.lock:
            if msg not in self.failed:
                self.failed.append(msg)
        log("FAIL", msg)

    def note(self, msg):
        with self.lock:
            if msg not in self.notes:
                self.notes.append(msg)

    def set_id(self, uid, source):
        if not uid:
            return
        uid = str(uid)
        with self.lock:
            if self.user_id is None:
                self.user_id = uid
                log(f"resolved user id {uid} via {source}")
            elif self.user_id != uid:
                self.note(f"{source} reported user id {uid} (differs from {self.user_id}) - possible handle reuse")

    # -------------------------------------------------- observations
    def observe(self, date, fields, source, evidence=None, verified=True, ts=None, confidence=None):
        """date: YYYY-MM-DD.  fields: subset of FIELDS.  verified: owner id confirmed."""
        if not date:
            return
        f = {}
        for k in FIELDS:
            v = fields.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = v.strip()
                if k == "name":
                    v = re.sub(r"(?i)^\s*verified account\s+|\s+verified account\s*$", "", v).strip()
                if k == "screen_name":
                    v = v.lstrip("@")
                    if not re.fullmatch(SN, v):
                        continue
            f[k] = v
        if str(source).startswith(("life-scan", "frozen-handle", "memory.lol")):
            # v4 guard: tweet-side sightings prove the @handle only - never stamp today's name/bio/avatar on old dates
            f = {k: v for k, v in f.items() if k == "screen_name"}
        if not f:
            return
        conf = confidence or ("high" if verified else "medium")
        with self.lock:
            self.obs.append({"date": date, "ts": ts or date.replace("-", ""), "source": source,
                             "evidence": evidence, "verified": verified, "confidence": conf, "fields": f})

    def handles_seen(self, verified_only=True):
        out = {}
        with self.lock:
            for o in self.obs:
                sn = o["fields"].get("screen_name")
                if sn and (o["verified"] or not verified_only):
                    out.setdefault(sn.lower(), sn)
            for h in self.memorylol:
                out.setdefault(h.lower(), h)
        return out

    def known_avatar_ids(self):
        ids = set()
        with self.lock:
            for o in self.obs:
                if o["verified"] and o["fields"].get("avatar"):
                    m = re.search(r"profile_images/(\d+)/", o["fields"]["avatar"])
                    if m:
                        ids.add(m.group(1))
        return ids
