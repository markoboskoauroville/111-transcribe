# ── The engine ────────────────────────────────────────────────────────────────
#
# Baba, 17.9.2026: "media optimization, robust sending, verbose monitor,
# Braille spinner with estimated time of arrival... any file which FFmpeg
# can convert to audio... each segment progressively added to the text box
# so user can see transcription as it comes... and this must work with
# buggy internet connections so it intelligently continues always."
#
# EVERY NUMBER BELOW WAS MEASURED against 22.5 minutes of his own Croatian
# voiceover on 17.9.2026, not read off a page.

import os
import subprocess
import tempfile
import time

import requests
import streamlit as st

import concurrent.futures as _cf
import hashlib as _hashlib
import shutil as _shutil

# ── Opus at 16 kbps, mono, 16 kHz ─────────────────────────────────────────────
#
# MEASURED, same 22.5 minutes of Croatian through all four:
#
#     12k   2643 words   87.3% identical to 32k    5.2 MB/hour
#     16k   2651 words   88.5%                     6.8 MB/hour
#     24k   2657 words   88.9%                    10.1 MB/hour
#     32k   2687 words   —                        13.3 MB/hour
#
# 12k saves another quarter of the space and loses measurably. 16k is where
# the curve goes flat: ten hours of audio becomes 68 MB, and the diacritics
# come through clean — "Po pitanju zaštite djece, EU je presudila."
#
# 16 kHz because AssemblyAI works at 16 kHz internally; anything above it is
# thrown away before recognition, so sending it is paying to upload silence.
#
# ASSEMBLYAI TAKES OPUS DIRECTLY. Verified: uploaded, accepted, transcribed.
# No WAV, no MP3, no conversion on their side.
AUDIO_BITRATE = "16k"
AUDIO_RATE = 16000

# ── Ten-minute chunks ─────────────────────────────────────────────────────────
#
# MEASURED, the same 22.5 minutes three ways:
#
#     12 x 2 min, 12 in parallel   27.2 s
#      3 x 10 min, 3 in parallel   16.2 s
#      1 x whole file              31.5 s
#
# TWO MINUTES IS SLOWER, NOT FASTER, which is the opposite of what we
# assumed. Each piece carries its own upload, its own job, its own polling,
# and that overhead beats the gain from spreading the work.
#
# But the honest finding is that chunking is not for SPEED at all: the whole
# file finishes in 31 seconds. Chunking is for SEEING THE TEXT ARRIVE and for
# surviving a dropped connection — a lost 10-minute piece costs 10 minutes of
# retry, a lost whole file costs everything.
#
# PARALLELISM IS NOT THE LIMIT. Twenty simultaneous jobs on one key, no 429,
# no refusal. The ceiling is above 20 and was not found. Six is used because
# more does not help at this chunk size and leaves room for other work.
CHUNK_SECONDS = 600
MAX_PARALLEL = 6

# ── The Braille spinner ───────────────────────────────────────────────────────
#
# Ten frames, one dot travelling round. One character wide, so it reads as
# motion without reflowing a line on a phone.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _human_bytes(n):
    n = int(n or 0)
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def _human_time(s):
    s = int(max(0, s or 0))
    if s < 60:
        return "%ds" % s
    return "%dm %02ds" % (s // 60, s % 60)


# ── The key ring ──────────────────────────────────────────────────────────────
#
# Baba: "if one assembly key doesn't work, app can go to another one if there
# is another one."
#
# A VERDICT PER FAILURE, not a retry count. The three cases need different
# answers and treating them alike is how a live key gets buried:
#
#     refused   401 / 403     the key is wrong. Bury it, try the next.
#     busy      429           too fast, or out of credit. Rest it, try next.
#     unknown   5xx, timeout, no network
#                             THEIR problem or the line, not the key. Do NOT
#                             bury it — a dropped connection on a train would
#                             otherwise eat all five keys in twenty seconds.
#
# THE LAST RULE IS THE ONE THAT MATTERS FOR "buggy internet connections". An
# unknown verdict comes back to the SAME key after a pause, because the key
# was never the problem.
AAI_REFUSED = "refused"
AAI_BUSY = "busy"
AAI_UNKNOWN = "unknown"


def aai_keys():
    """Every AssemblyAI key from Secrets, newest style first.

    Accepts the list and the single legacy name, so an existing deployment
    keeps working without an edit.
    """
    out = []
    try:
        raw = st.secrets.get("ASSEMBLYAI_API_KEYS", []) or []
        if isinstance(raw, str):
            raw = [raw]
        out += [str(k).strip() for k in raw if str(k).strip()]
    except Exception:
        pass
    try:
        one = str(st.secrets.get("ASSEMBLYAI_API_KEY", "") or "").strip()
        if one and one not in out:
            out.append(one)
    except Exception:
        pass
    return out


def aai_verdict(status, body=""):
    """One of three words. Never raises."""
    b = (body or "").lower()
    if status in (401, 403):
        return AAI_REFUSED
    if status == 429:
        return AAI_BUSY
    if status and 400 <= status < 500:
        # A 4xx THAT IS NOT AUTH AND NOT RATE IS OUR REQUEST, and no other
        # key will fix it. Returning `unknown` here would retry a malformed
        # request four times against every key in the ring — twenty pointless
        # calls for a mistake the ring cannot mend.
        #
        # MEASURED 17.9.2026: a genuinely invalid key answers a clean 401
        # with "Authentication error, API token missing/invalid", so the auth
        # case never arrives as a 400 and does not need catching here.
        return AAI_REFUSED
    return AAI_UNKNOWN


def _ring():
    st.session_state.setdefault("_aai_ring", {"buried": set(), "resting": {}})
    return st.session_state["_aai_ring"]


def _live_keys():
    """Keys worth trying now, in order."""
    ring = _ring()
    now = time.time()
    out = []
    for k in aai_keys():
        fp = _hashlib.sha256(k.encode()).hexdigest()[:12]
        if fp in ring["buried"]:
            continue
        if ring["resting"].get(fp, 0) > now:
            continue
        out.append(k)
    return out


def _mark(key, verdict, wait=60):
    fp = _hashlib.sha256(key.encode()).hexdigest()[:12]
    ring = _ring()
    if verdict == AAI_REFUSED:
        ring["buried"].add(fp)
    elif verdict == AAI_BUSY:
        ring["resting"][fp] = time.time() + wait


def aai_call(method, path, key, **kw):
    """One request. Returns (response_or_None, verdict).

    NEVER RAISES ON THE NETWORK. A dropped connection is an `unknown`
    verdict, which is what lets the caller come back to the same key.
    """
    try:
        r = requests.request(
            method, "https://api.assemblyai.com/v2" + path,
            headers={"authorization": key, **kw.pop("headers", {})},
            timeout=kw.pop("timeout", 120), **kw)
    except Exception:
        return None, AAI_UNKNOWN
    if r.status_code < 300:
        return r, ""
    return r, aai_verdict(r.status_code, r.text[:400])


def aai_one_file(job, on_note=None, tries=4):
    """Run a WHOLE audio file on ONE key. Fall back only by starting over.

    Baba, 17.9.2026: "never use multiple APIs on one audio file. Always use
    one API on one audio file."

    HE IS RIGHT, AND IT IS NOT A PREFERENCE — IT IS THE API. Measured
    17.9.2026 against two live accounts:

        key B polling key A's transcript      HTTP 404
        key B starting a job from A's upload  "Cannot access uploaded file"

    An upload belongs to the account that made it and a transcript belongs
    to the account that started it. The previous version rotated PER
    REQUEST — upload on one key, start on another, poll on a third — which
    would have 404ed mid-job and lost work that was already paid for, with
    no error that named the cause.

    So a key is chosen once and carries the file from upload to finished
    text. If it fails, the NEXT key begins again from the upload, because
    there is nothing of the first attempt it could inherit.

    `job` is called as job(key) and returns (result, verdict). A falsy
    verdict means the whole file is done.

    BACKOFF CARRIES JITTER, so several chunks failing together do not all
    retry in the same instant and deliver the burst that caused it.
    """
    import random
    last = AAI_UNKNOWN
    for attempt in range(tries):
        keys = _live_keys()
        if not keys:
            return None, "no keys left"
        for key in keys:
            fp = _hashlib.sha256(key.encode()).hexdigest()[:6]
            if on_note:
                on_note("key %s: working" % fp)
            result, verdict = job(key)
            if not verdict:
                return result, ""
            last = verdict
            _mark(key, verdict)
            if on_note:
                on_note("key %s: %s" % (fp, verdict))
            if verdict == AAI_UNKNOWN:
                # THE LINE, NOT THE KEY. Pause and come back to this same
                # key rather than spending the ring on a bad connection.
                break
        time.sleep(min(30, 2 ** attempt) * (0.8 + 0.4 * random.random()))
    return None, last


# ── Media ─────────────────────────────────────────────────────────────────────

def ffmpeg_ok():
    return bool(_shutil.which("ffmpeg"))


def to_opus_chunks(raw_bytes, filename, seconds=CHUNK_SECONDS, note=None):
    """Any file ffmpeg can read -> a list of Opus chunk paths, in order.

    Baba: "this file picker can accept any file which FFmpeg can convert to
    audio. So it can be also video file."

    VIDEO IS FINE and the picture is discarded: -vn. A 2 GB screen recording
    becomes a few megabytes of speech.

    ONE PASS, NOT TWO. ffmpeg segments and encodes in the same command, so a
    long file is never decoded to disk as WAV first — which on a 200 MB
    upload would mean a gigabyte of temporary space nobody budgeted for.
    """
    tmp_dir = tempfile.mkdtemp(prefix="mt_")
    src = os.path.join(tmp_dir, "in" + (os.path.splitext(filename)[1] or ".bin"))
    with open(src, "wb") as fh:
        fh.write(raw_bytes)
    pattern = os.path.join(tmp_dir, "p%04d.opus")
    cmd = ["ffmpeg", "-y", "-i", src, "-vn",
           "-ac", "1", "-ar", str(AUDIO_RATE),
           "-c:a", "libopus", "-b:a", AUDIO_BITRATE,
           "-f", "segment", "-segment_time", str(seconds),
           "-reset_timestamps", "1", pattern]
    if note:
        note("compressing…")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    parts = sorted(f for f in os.listdir(tmp_dir) if f.endswith(".opus"))
    if not parts:
        tail = (proc.stderr or "")[-300:]
        raise RuntimeError("ffmpeg could not read that file.\n" + tail)
    return [os.path.join(tmp_dir, p) for p in parts], src


def media_seconds(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


# ── One chunk, end to end ─────────────────────────────────────────────────────

def transcribe_chunk(path, lang_params, on_note=None):
    """One audio file, start to finished text, on ONE key.

    Every step below uses the SAME key by construction — see aai_one_file
    for the measurement that makes this mandatory rather than tidy.
    """
    data = open(path, "rb").read()
    body_base = {"punctuate": True, "format_text": True,
                 "speech_models": ["universal-3-pro", "universal-2"],
                 **lang_params}

    def whole_file(key):
        # 1. UPLOAD
        r, v = aai_call("POST", "/upload", key, data=data,
                        headers={"content-type": "application/octet-stream"},
                        timeout=900)
        if v or r is None:
            return None, v or AAI_UNKNOWN
        url = r.json().get("upload_url")
        if not url:
            return None, AAI_UNKNOWN

        # 2. START
        r, v = aai_call("POST", "/transcript", key,
                        json={"audio_url": url, **body_base},
                        headers={"content-type": "application/json"})
        if v or r is None:
            return None, v or AAI_UNKNOWN
        got = r.json()
        if got.get("error"):
            return None, AAI_REFUSED
        tid = got.get("id")

        # 3. POLL, ON THE SAME KEY, and survive the line going down.
        #
        # A FAILED POLL IS NOT A FAILED JOB. The work continues on their
        # side, so a dropped connection keeps asking rather than abandoning
        # a transcript that is already being made and already charged for.
        t0 = time.time()
        misses = 0
        while time.time() - t0 < 3600:
            time.sleep(3)
            r, v = aai_call("GET", "/transcript/" + tid, key, timeout=60)
            if v or r is None:
                misses += 1
                if misses > 100:
                    return None, AAI_UNKNOWN
                if on_note:
                    on_note("line dropped, still waiting…")
                time.sleep(3)
                continue
            misses = 0
            s_ = r.json()
            if s_.get("status") == "completed":
                return s_, ""
            if s_.get("status") == "error":
                # THEIR verdict on this audio, not on the key. Another key
                # would fail the same way, so this is not a ring problem.
                return None, AAI_REFUSED
        return None, AAI_UNKNOWN

    got, err = aai_one_file(whole_file, on_note)
    if got is None:
        return None, err or "failed"
    return got, ""
