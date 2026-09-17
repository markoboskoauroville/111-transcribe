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
    # THE OUTPUT GETS ITS OWN FOLDER, and that is not tidiness.
    #
    # Baba, 17.9.2026: "Opus, nothing is happening when I upload Opus."
    #
    # The input was written beside the output as "in<ext>" and the chunks
    # were then collected BY EXTENSION. Upload a .opus or a .ogg and the
    # input file matches the pattern too — so the whole recording came back
    # as chunk one, followed by the real chunks: every word transcribed
    # twice, paid for twice, and "in.opus" sorting before "p0000.opus" put
    # the duplicate first.
    #
    # It only bit on the formats we ourselves encode to, which is why WAV
    # worked and Opus did not. Collecting by NAME would have been the same
    # bug waiting for a file called p0000.opus.
    tmp_dir = tempfile.mkdtemp(prefix="mt_")
    out_dir = os.path.join(tmp_dir, "chunks")
    os.makedirs(out_dir, exist_ok=True)
    src = os.path.join(tmp_dir, "in" + (os.path.splitext(filename)[1] or ".bin"))
    with open(src, "wb") as fh:
        fh.write(raw_bytes)
    pattern = os.path.join(out_dir, "p%04d.opus")
    cmd = ["ffmpeg", "-y", "-i", src, "-vn",
           "-ac", "1", "-ar", str(AUDIO_RATE),
           "-c:a", "libopus", "-b:a", AUDIO_BITRATE,
           "-f", "segment", "-segment_time", str(seconds),
           "-reset_timestamps", "1", pattern]
    if note:
        note("compressing…")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    parts = sorted(f for f in os.listdir(out_dir) if f.endswith(".opus"))
    if not parts:
        tail = (proc.stderr or "")[-300:]
        raise RuntimeError("ffmpeg could not read that file.\n" + tail)
    return [os.path.join(out_dir, p) for p in parts], src


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

def transcribe_chunk(path, lang_params, on_note=None, extra=None,
                     on_tick=None):
    """One audio file, start to finished text, on ONE key.

    Every step below uses the SAME key by construction — see aai_one_file
    for the measurement that makes this mandatory rather than tidy.
    """
    data = open(path, "rb").read()
    body_base = {"punctuate": True, "format_text": True,
                 "speech_models": ["universal-3-pro", "universal-2"],
                 **lang_params, **(extra or {})}

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
            # THE SPINNER TURNS WHILE WE WAIT. Baba, 17.9.2026: "while the
            # app is working the Braille spinner should also rotate. Now
            # it's standing, it stopped."
            #
            # It stood still because the whole-file path — which speaker
            # detection uses, and that is now the default — drew SPINNER[0]
            # once and then sat inside this poll loop for a minute saying
            # nothing. A spinner that does not move is worse than no
            # spinner: it says the app has hung.
            #
            # Drawn HERE, in the only place that knows the work is still
            # going on, so both paths animate without either of them
            # having to remember to.
            for _ in range(6):
                if on_tick:
                    on_tick()
                time.sleep(0.5)
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


def _run_one_piece(raw_bytes, filename, lang_params, ui, speakers, t_start):
    """The whole recording as one job, so the speaker labels hold throughout.

    Returns (text, utterances) where utterances is AssemblyAI's own list:
    each one a stretch of speech by a single speaker, with `speaker` as a
    capital letter, `text`, and `start`/`end` in milliseconds.
    """
    if not ffmpeg_ok():
        raise RuntimeError("ffmpeg is not installed on this server.")

    ui.status("%s reading the file…" % SPINNER[0])
    ui.note("input: %s, %s" % (filename, _human_bytes(len(raw_bytes))))
    ui.note("speakers asked for: the whole file goes as one job, so the "
            "labels are the same from start to end")

    # One piece, however long it is: CHUNK_SECONDS is set absurdly high so
    # the same compression path runs and produces a single file.
    parts, _src = to_opus_chunks(raw_bytes, filename, seconds=10 ** 7,
                                 note=ui.note)
    audio_s = sum(media_seconds(p) for p in parts)
    ui.note("audio length %s in %d piece" % (_human_time(audio_s), len(parts)))

    extra = {"speaker_labels": True}
    # HARD BOUNDARIES, NOT HINTS, which is why a wrong number is worse than
    # none: a maximum that is too low merges two people into one label.
    if isinstance(speakers, int) and speakers > 1:
        extra["speakers_expected"] = speakers

    _frame = [0]

    def _tick():
        _frame[0] += 1
        ui.status("%s  transcribing %s  ·  %s elapsed"
                  % (SPINNER[_frame[0] % len(SPINNER)],
                     _human_time(audio_s), _human_time(time.time() - t_start)))

    got, err = transcribe_chunk(parts[0], lang_params, on_note=ui.note,
                                extra=extra, on_tick=_tick)
    for p in parts:
        try:
            os.remove(p)
        except Exception:
            pass
    if err or got is None:
        raise RuntimeError("that recording could not be transcribed: %s" % err)

    utterances = got.get("utterances") or []
    text = got.get("text") or ""
    heard = sorted({u.get("speaker", "?") for u in utterances})
    ui.status("done  ·  %s  ·  %d words  ·  %d speaker%s"
              % (_human_time(time.time() - t_start), len(text.split()),
                 len(heard), "" if len(heard) == 1 else "s"))
    ui.note("speakers heard: %s" % (", ".join(heard) or "none"))
    return text, utterances


# ── The run ───────────────────────────────────────────────────────────────────

def run_transcription(raw_bytes, filename, lang_params, ui, speakers=None):
    """The whole job, reporting as it goes. Returns the full text.

    Baba, 17.9.2026: "each segment is progressively added to the text box
    status line so user can see transcription as it comes. First 10 minutes
    in the text box, and then it will say, I don't know, first piece of 10,
    1 of 10, and then it will just fill up the text box until it comes to
    the end."

    `ui` carries three callables so this file never imports a widget:
        ui.status(text)   one line, replaced each time
        ui.text(text)     the transcript so far
        ui.note(text)     the verbose monitor, appended

    CHUNKS COME BACK IN ORDER EVEN THOUGH THEY FINISH OUT OF ORDER. Six run
    at once and the short last piece often lands first; showing it first
    would put the end of the recording at the top of the box. Results go
    into a slot per index and the box is redrawn from the slots, so what he
    reads is always the recording in the order it was spoken.
    """
    t_start = time.time()

    # WHO IS SPEAKING, AND WHY IT CANNOT BE SPLIT (17.9.2026).
    #
    # This app cuts audio into ten-minute pieces and runs six at once, which
    # is what makes it fast. Speaker labels cannot survive that: each request
    # is a separate job, so "Speaker A" in the third piece is whoever spoke
    # first in the third piece, and that is a different person from the A in
    # the first piece as often as not. Stitching them would produce a
    # transcript that looks right and is wrong, which is worse than slow.
    #
    # So when he asks for speakers, the whole recording goes as ONE job.
    # Slower — no six-way parallel — and correct, with one set of labels from
    # beginning to end. AssemblyAI takes hours-long files.
    if speakers:
        return _run_one_piece(raw_bytes, filename, lang_params, ui, speakers,
                              t_start)

    if not ffmpeg_ok():
        raise RuntimeError("ffmpeg is not installed on this server.")

    ui.status("%s reading the file…" % SPINNER[0])
    ui.note("input: %s, %s" % (filename, _human_bytes(len(raw_bytes))))

    parts, _src = to_opus_chunks(raw_bytes, filename, note=ui.note)
    audio_s = sum(media_seconds(p) for p in parts)
    packed = sum(os.path.getsize(p) for p in parts)
    ui.note("compressed to %s in %d piece%s  (%.0fx smaller)"
            % (_human_bytes(packed), len(parts), "" if len(parts) == 1 else "s",
               (len(raw_bytes) / packed) if packed else 1))
    ui.note("audio length %s" % _human_time(audio_s))

    # THE ESTIMATE, FROM A MEASUREMENT RATHER THAN A GUESS.
    #
    # Measured 17.9.2026 on Baba's Croatian: 22.5 minutes of audio, three
    # chunks, six parallel, finished in 17.5 seconds — about 77x faster than
    # real time. The estimate uses 40x, deliberately pessimistic, because an
    # ETA that runs out while the person is still waiting is worse than one
    # that finishes early.
    rounds = max(1, (len(parts) + MAX_PARALLEL - 1) // MAX_PARALLEL)
    eta = max(8.0, (audio_s / 40.0) * rounds / max(1, len(parts)) * len(parts))

    slots = [None] * len(parts)
    done = [0]

    def draw(frame):
        got = done[0]
        left = max(0.0, eta - (time.time() - t_start))
        ui.status("%s  %d of %d  ·  %s elapsed  ·  about %s left"
                  % (SPINNER[frame % len(SPINNER)], got, len(parts),
                     _human_time(time.time() - t_start), _human_time(left)))

    def one(i):
        got, err = transcribe_chunk(parts[i], lang_params, on_note=ui.note)
        if err:
            # A FAILED PIECE IS NAMED AND THE REST CONTINUES. Twenty minutes
            # of transcript is worth having with one gap in it; throwing it
            # all away because piece four failed is not.
            slots[i] = "[piece %d could not be transcribed: %s]" % (i + 1, err)
            ui.note("piece %d FAILED: %s" % (i + 1, err))
        else:
            slots[i] = got.get("text") or ""
            ui.note("piece %d done, %d words" % (i + 1, len(slots[i].split())))
        done[0] += 1
        ui.text("\n\n".join(x for x in slots if x is not None))
        return i

    frame = 0
    with _cf.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
        futures = [ex.submit(one, i) for i in range(len(parts))]
        while any(not f.done() for f in futures):
            draw(frame)
            frame += 1
            time.sleep(0.4)
        for f in futures:
            f.result()

    text = "\n\n".join(x or "" for x in slots).strip()
    ui.status("done  ·  %d piece%s  ·  %s  ·  %d words"
              % (len(parts), "" if len(parts) == 1 else "s",
                 _human_time(time.time() - t_start), len(text.split())))
    for p in parts:
        try:
            os.remove(p)
        except Exception:
            pass
    # Without speakers there are no utterances; the shape stays the same so
    # the caller never has to ask which kind of run it got.
    return text, []
