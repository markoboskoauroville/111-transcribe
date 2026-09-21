# 111 TRANSCRIBE

Upload audio or video, get a transcript — with speakers, timecodes, subtitles and translation.
Built on **AssemblyAI**, deployed on **Streamlit Cloud**.

By **Mantra Productions**, Zagreb.

Files up to **2000 MB**. That number lives in `.streamlit/config.toml` and it has to, because
Streamlit uploads the whole file to the server before `app.py` sees a single byte — a splitter
written inside the app would run after the limit had already refused the file.

**Nothing leaves this app except the audio, and it goes only to AssemblyAI.** There is no
spreadsheet, no analytics, no IP lookup and no Google. The transcript is yours on the page.

---

## The secrets file

Streamlit reads its secrets from **`.streamlit/secrets.toml`**. On Streamlit Cloud the same text
goes into **Manage app → Settings → Secrets**; there is no file to upload.

**Copy the block below and paste your own values between the quotation marks.** Every place you
have to fill in is written as `" "` — two quotation marks with one space between them. Click
between them and paste. Do not remove the quotes.

```toml
# ── Who gets in ───────────────────────────────────────────────────────────────
# THIS USERNAME IS THE ONLY THING GUARDING YOUR ASSEMBLYAI CREDIT. There is no
# password behind it. Anybody who knows it, is told it, or guesses it can
# transcribe on your account until the credit is gone.
#
# So choose it the way you would choose a password: long, and not a word anyone
# would try. "marko" is a bad username here. A short phrase with a number in it
# is a good one.
USERNAME = " "


# ── AssemblyAI ────────────────────────────────────────────────────────────────
# One key or several. With several, the app moves to the next one when a key is
# refused or runs out of credit, so a dead key is a pause rather than a stop.
# Keep the brackets and the commas; add or remove lines as you have keys.
ASSEMBLYAI_API_KEYS = [
    " ",
    " ",
]

# The older single-key name still works if you prefer it. Use one style or the
# other; if both are present, every key from both is tried.
# ASSEMBLYAI_API_KEY = " "
```

That is the whole file. Two things.

**`.streamlit/secrets.toml` is in `.gitignore` and must stay there.** Nothing in this repository
should ever hold a key.

### If something is missing

The app names the secret rather than failing vaguely — somebody looking at a locked app they own
needs to know it is unconfigured, not broken. No `USERNAME` means nobody gets in, on purpose:
a missing secret is a locked door, not an open one with a famous key.

---

## Running it on your own machine

    pip install -r requirements.txt
    streamlit run app.py

`packages.txt` and `runtime.txt` are read only by Streamlit Cloud, to install ffmpeg and pick the
Python version. They do nothing locally — you need ffmpeg on your `PATH` yourself.

## The files

    app.py                  the interface, the door, the tabs, subtitles and translation
    transcribe_engine.py    AssemblyAI: the key ring, the pieces, the polling, the verdicts
    .streamlit/config.toml  the dark theme and the 2000 MB upload cap
    requirements.txt        four packages
    packages.txt            apt packages for Streamlit Cloud (ffmpeg)
    runtime.txt             the Python version for Streamlit Cloud

## What was taken out on 21.9.2026

This app used to write a row to a Google Sheet for every transcription — filename, the first and
last three words of the transcript, and **the visitor's IP address, city, country, ISP and which
broadcaster they appeared to work for**, looked up through a third party on every run. All of it
is gone, along with `gspread`, `google-auth`, the service-account secret and the Google font
import that was sending every visitor's address to Google before the page drew a character.

Three dead functions went with it, none of them casualties of the removal — they were what the
removal made visible: a stereo-to-mono ffmpeg pass that AssemblyAI does itself and does not
charge extra for, a read-along TTS player nothing ever rendered, and the original chunked
uploader that the engine replaced and nobody deleted.

The credit gauge went too, and that is worth explaining rather than just listing. It summed the
durations in the spreadsheet, multiplied by a rate typed into the source, subtracted that from a
credit figure also typed into the source, and drew the result as a fuel gauge. It had never
spoken to AssemblyAI; when it drifted, there was a box to type the true balance into by hand.
With the sheet gone it would have read **full, forever** — and a gauge that always reads full is
worse than no gauge, because it is read at a glance and believed. What replaces it is the one
number this app can state as a fact: how much audio *this browser tab* has sent since it opened,
and what that cost at the published rate. Your real balance is on the AssemblyAI dashboard, which
the tab links to rather than guessing at.

1,700 lines became 1,422.
