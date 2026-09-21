# 111 TRANSCRIBE

Upload audio or video, get a transcript. Built on **AssemblyAI**, deployed on **Streamlit Cloud**,
with every transcription logged to a Google Sheet. Behind a password, because the keys it holds
are chargeable.

By **Mantra Productions**, Zagreb.

Files up to **2000 MB**. That number is in `.streamlit/config.toml` and it has to be, because
Streamlit uploads the whole file to the server before `app.py` sees a single byte — a splitter
written inside the app runs after the limit has already refused the file.

---

## The secrets file

Streamlit reads its secrets from **`.streamlit/secrets.toml`**. On Streamlit Cloud the same text
goes into **Manage app → Settings → Secrets**; there is no file to upload.

**Copy the block below and paste your own values between the quotation marks.** Every place you
have to fill in is written as `" "` — two quotation marks with one space between them. Click
between them and paste; do not remove the quotes.

```toml
# ── The door ──────────────────────────────────────────────────────────────────
# Without this nobody gets in, on purpose. There is no default and no fallback:
# a missing password is a locked door, not an open one with a famous key.
APP_PASSWORD = " "


# ── AssemblyAI ────────────────────────────────────────────────────────────────
# One key or several. With several, the app moves to the next one when a key is
# refused or runs out of credit, so a dead key is a pause rather than a stop.
# Keep the brackets and the commas; add or remove lines as you have keys.
ASSEMBLYAI_API_KEYS = [
    " ",
    " ",
]

# The old single-key name still works if you prefer it. Use one style or the
# other; if both are present, every key from both is tried.
# ASSEMBLYAI_API_KEY = " "


# ── The log ───────────────────────────────────────────────────────────────────
# The Google Sheet every transcription is written to. Paste the whole address
# from the browser bar, the long one with /d/ in the middle.
GOOGLE_SHEET_URL = " "


# ── The Google service account ────────────────────────────────────────────────
# This is the robot user that writes to the sheet, and it is a TABLE rather than
# a single value — the [heading] line below must stay exactly as it is.
#
# Open the .json file Google gave you when you made the service account and copy
# each value across. They are the same field names in both places.
#
# TWO THINGS GO WRONG HERE AND BOTH ARE SILENT:
#
#   1. private_key must stay on ONE LINE, with every line break written as the
#      two characters \n, exactly as it appears in the .json. If your editor
#      turns those into real line breaks the key stops parsing and the sheet
#      simply never fills in.
#   2. The sheet must be SHARED with client_email below, as an Editor, the same
#      way you would share it with a person. Google does not warn you; the app
#      just cannot open it.
[gcp_service_account]
type                        = "service_account"
project_id                  = " "
private_key_id              = " "
private_key                 = " "
client_email                = " "
client_id                   = " "
auth_uri                    = "https://accounts.google.com/o/oauth2/auth"
token_uri                   = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url        = " "
universe_domain             = "googleapis.com"
```

The lines already filled in are the same for every Google service account and can be left alone.

**`.streamlit/secrets.toml` is in `.gitignore` and must stay there.** Nothing in this repository
should ever hold a key.

### If something is missing

The app says which secret it is rather than failing vaguely — a locked app you own needs to tell
you it is unconfigured, not broken.

---

## Running it on your own machine

    pip install -r requirements.txt
    streamlit run app.py

`packages.txt` and `runtime.txt` are for Streamlit Cloud, which reads them to install ffmpeg and
pick the Python version. They do nothing locally.

## The files

    app.py                the whole interface, the password, the sheet, the translation
    transcribe_engine.py  AssemblyAI: the key ring, the upload, the polling, the verdicts
    .streamlit/config.toml  the dark theme and the 2000 MB upload cap
    requirements.txt      pinned Python packages
    packages.txt          apt packages for Streamlit Cloud (ffmpeg)
    runtime.txt           the Python version for Streamlit Cloud
