import streamlit as st
import requests
import time
import os
import json
import asyncio
import threading
import re
import hmac
import subprocess
import tempfile

# ── Version ───────────────────────────────────────────────────────────────────
#
# Baba, 17.9.2026: "every time you finish a version, print the version number
# here so I can check visually."
#
# THE BADGE SAID v3.3 THROUGH NINE VERSIONS. It was a literal buried in the
# middle of an HTML string, so every version after it was named in a commit
# message and nowhere he could see. His check would have compared a number
# on his phone against a number in a message and found them equal only by
# accident.
#
# ONE NAME, AT THE TOP, WHERE SOMEBODY CHANGING A VERSION WILL SEE IT.
APP_VERSION = "v3.16"

# ── Secrets ───────────────────────────────────────────────────────────────────
# ONE KEY WAS A HARD REQUIREMENT HERE — st.secrets["..."] with square
# brackets raises if it is missing, so a deployment without that exact
# name died on line 21 with a KeyError and no page at all. The ring reads
# both names and decides at call time; see engine.aai_keys.
# IMPORTED UNDER A DIFFERENT NAME because app.py already has a
# run_transcription — its button handler. Two functions with one name is a
# collision pyflakes catches and a reader does not.
# A MISSING MODULE MUST SAY SO, NOT SHOW A REDACTED ImportError.
#
# On 17.9.2026 the deployed app died here with "ImportError ... original
# error message is redacted to prevent data leaks", which tells the person
# looking at it precisely nothing. The cause was a __pycache__ directory
# swept into the repository by a `git add -A` of mine.
#
# The import is guarded now so the page still draws and NAMES the problem.
# Streamlit redacts the exception text, so the app has to supply its own.
# THE MODULE IS RENAMED, AND THAT RENAME IS THE FIX.
#
# The server kept failing with "cannot import name 'run_transcription'
# from 'engine'" while the source on that exact path plainly defined it at
# module level. The cause was STALE BYTECODE: Streamlit Cloud runs Python
# 3.14 and had a __pycache__/engine.cpython-314.pyc left from v3.6, when
# engine.py had no run_transcription yet. Pulling new source did not
# dislodge it.
#
# I MADE THAT POSSIBLE by committing a __pycache__ directory in v3.7 with
# a `git add -A`. It is removed and ignored now, but a cached .pyc already
# sitting on the server is not something a git push can reach.
#
# A NEW MODULE NAME HAS NO CACHED BYTECODE. Nothing else about the file
# changed.
try:
    from transcribe_engine import run_transcription as engine_run
    ENGINE_ERROR = ""
except Exception as _exc:                                   # noqa: BLE001
    ENGINE_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

    def engine_run(*_a, **_kw):
        raise RuntimeError(ENGINE_ERROR)

# API_KEY AND HEADERS WERE HERE AND ARE GONE, 21.9.2026. They took the FIRST key off the ring at
# import time and built an authorization header from it, which was how the app talked to
# AssemblyAI before the engine existed. The engine owns the ring now and chooses a key per piece,
# so a header built once at startup could only ever have been the wrong one — and nothing read it
# except the dead uploader below it.
# ADMIN_PASSWORD was read here and used NOWHERE in 1,343 lines, with a
# default of "admin123". Removed with the gate that replaces it: a
# variable that looks like a password check and is not one is worse than
# no check at all, because it stops anybody asking where the check is.
# THE SETTINGS FILE IS GONE, AND IT WENT WITH GOOGLE RATHER THAN BEING TIDIED AWAY.
#
# It held exactly two values: the sheet URL and the title. With the sheet removed, one remained,
# and it was never written by anything — `save_settings` was only ever called by the balance
# calibration, which the sheet also fed. So the whole apparatus was a JSON file in /tmp, two
# functions and a try/except, standing behind a single string constant.
#
# It also could not work on Streamlit Cloud, which wipes /tmp on every restart. A cache that is
# always cold is not a cache; it is a file nobody reads.
APP_TITLE = "111 TRANSCRIBE"

# ── WHAT STOOD HERE, AND WHY ITS REMOVAL IS A FEATURE ────────────────────────────────
#
# Four functions — is_private, get_client_ip, get_ip_info, detect_owner — plus format_duration,
# which nothing called at all.
#
# NONE OF THEM SERVED THE PERSON USING THE APP. They existed to fill six columns of the Google
# Sheet: ip, city, country, org, isp, owner. Every transcription sent the visitor's IP address
# to ip-api.com, a third party, to find out which broadcaster they worked for.
#
# With the sheet gone there is nothing to fill, so this is not a loss of function — it is the
# removal of a network call to a third party, made on every run, about a person, that nobody
# downstream was going to read.

# ── TWO DEAD FUNCTIONS REMOVED, 21.9.2026 ────────────────────────────────────────────
#
# Neither was called from anywhere, in either file, and both predate this change — they are
# not casualties of removing Google, they are what removing Google made visible.
#
#   ensure_mono(audio_bytes, filename)     44 lines. Shelled out to ffmpeg to fold stereo down
#                                          to mono before upload. AssemblyAI does that itself
#                                          and charges by duration, not by channel, so it
#                                          bought nothing and cost a subprocess per file.
#
#   build_tts_player(text, audio_b64, ..)  66 lines of inline HTML and JavaScript for a
#                                          read-along player that highlighted each word as it
#                                          was spoken. Nothing ever rendered it. It was the
#                                          only reason `import base64` was here.
#
# Kept in the history, where dead code belongs, rather than in the file everybody reads.

VOICE_MAP = {
    "Hrvatski": {"🚺 Female": "hr-HR-GabrijelaNeural", "🚹 Male": "hr-HR-SreckoNeural"},
    "English":  {"🚺 Female": "en-US-AriaNeural",       "🚹 Male": "en-US-GuyNeural"},
    "Italiano": {"🚺 Female": "it-IT-ElsaNeural",        "🚹 Male": "it-IT-DiegoNeural"},
    "Deutsch":  {"🚺 Female": "de-DE-KatjaNeural",       "🚹 Male": "de-DE-ConradNeural"},
    "Français": {"🚺 Female": "fr-FR-DeniseNeural",      "🚹 Male": "fr-FR-HenriNeural"},
}

async def _tts_async(text: str, voice: str):
    import edge_tts
    communicate     = edge_tts.Communicate(text, voice)
    audio_chunks    = []
    word_boundaries = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            word_boundaries.append({
                "offset":   chunk["offset"]   / 10_000_000,
                "duration": chunk["duration"] / 10_000_000,
                "text":     chunk["text"],
            })
    return b"".join(audio_chunks), word_boundaries

def generate_tts(text: str, voice: str):
    result = {}
    def _run():
        result["value"] = asyncio.run(_tts_async(text, voice))
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join()
    return result["value"]

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title=APP_TITLE, page_icon="🎙️", layout="centered")
st.markdown(
    '<div style="position:fixed;top:8px;right:12px;color:#555;font-size:11px;'
    'z-index:9999;font-family:monospace;">' + APP_VERSION + '</div>',
    unsafe_allow_html=True)

# ── The door ──────────────────────────────────────────────────────────────────
#
# Baba, 13.9.2026: "add to the secrets password so this app will be opened
# by password only... whatever password is in the secret, user will be
# able to open that app. Without secret password, nothing."
#
# IT RUNS BEFORE ANYTHING ELSE IS DRAWN and calls st.stop(), so a wrong
# password does not merely hide the page — the rest of the script never
# executes. Nothing is fetched, no key is used, nothing is on screen to
# read from the page source.
#
# NO PASSWORD IN SECRETS MEANS NOBODY GETS IN. The old ADMIN_PASSWORD
# line had a default of "admin123" and was never used anywhere, which is
# the worst of both worlds: it looked like a gate and was not one, and
# the fallback was a password anyone could guess. A missing secret is now
# a locked door, not an open one with a famous key.
# ─────────────────────────────────────────────────────────────────────────────
# THE DOOR IS A USERNAME. Baba asked for it this way on 21.9.2026, having been
# shown what it costs, and it is his app and his AssemblyAI credit.
#
# WHAT IT MEANS, WRITTEN DOWN SO NOBODY HAS TO WORK IT OUT LATER: there is one
# secret, and it is the username. Anyone who knows it, is told it, or guesses
# it can transcribe on this account until the credit is gone. There is no
# second factor behind it.
#
# SO THE USERNAME SHOULD BE CHOSEN LIKE A PASSWORD — long, and not a word
# anybody would try. The README says so at the point where it is typed in.
#
# compare_digest rather than ==, because a plain comparison returns as soon as
# two characters differ, and the time it takes can be measured. That matters
# more here than it did with a password, not less: this string is now the only
# thing standing in the way.
# ─────────────────────────────────────────────────────────────────────────────
USERNAME = str(st.secrets.get("USERNAME", "") or "").strip()


def _door():
    if ENGINE_ERROR:
        # BEFORE THE PASSWORD, because a broken engine is not a locked
        # door and pretending otherwise wastes somebody's time typing.
        st.error("The transcription engine did not load.\n\n" + ENGINE_ERROR)
        st.caption("transcribe_engine.py is missing or failed to import on the server.")
        st.stop()
    if st.session_state.get("_in"):
        return
    st.markdown("### 🎙️ " + APP_TITLE)
    if not USERNAME:
        # SAY WHICH SECRET IS MISSING. Somebody looking at a locked app
        # they own needs to know it is unconfigured, not broken.
        st.error("No USERNAME is set in Secrets, so nobody can get in.")
        st.stop()
    # STILL type="password", even though it is a username. It is the only
    # credential this app has, so it is shoulder-surfable in exactly the way a
    # password is, and a field that shows it in the clear on a laptop in a
    # cutting room would be the wrong kind of honest.
    typed = st.text_input("Username", type="password", key="_pw")
    # ENTER SUBMITS, because a text_input reruns on Enter and that is how
    # this field is expected to behave. The button is for anyone whose
    # keyboard hides it.
    if st.button("Enter", use_container_width=True) or typed:
        if hmac.compare_digest(typed.strip(), USERNAME):
            st.session_state["_in"] = True
            st.rerun()
        elif typed:
            st.error("Not a username on this app.")
    st.stop()


_door()

st.markdown("""
<style>
  /* THE FONT WAS THE LAST GOOGLE IN THE APP, and it was the one nobody thinks of.
     An @import from fonts.googleapis.com sends every visitor's IP address and referring
     page to Google before a single word is rendered — the same thing the IP logging was
     removed for, done by a stylesheet instead of by a function.
     It is also render-blocking: the page waits on a third party to show its first
     character. The stack below asks the operating system for the font it already has —
     Inter on a machine that has it, San Francisco on a Mac, Segoe on Windows, Roboto on
     Android — which is faster than a download can ever be, and needs no network at all. */
  html,body,[class*="css"]{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;background:#1a1a1a;color:#e0e0e0;}
  .stApp{background:#1a1a1a;}
  h1{color:#ff6600;font-weight:700;letter-spacing:1px;border-bottom:2px solid #ff6600;padding-bottom:8px;margin-bottom:4px;}
  .subtitle{color:#888;font-size:.85rem;margin-bottom:24px;letter-spacing:2px;text-transform:uppercase;}
  .stRadio>label{color:#aaa;font-size:.8rem;letter-spacing:1px;text-transform:uppercase;}
  .stRadio div[role="radiogroup"] label{color:#ccc;}
  .stButton>button{background:#ff6600;color:#000;font-weight:700;border:none;border-radius:4px;
    padding:7px 14px;letter-spacing:0.5px;font-size:.85rem;text-transform:uppercase;}
  .stButton>button:hover{background:#cc5200;color:#fff;}
  .stTextArea textarea{background:#2a2a2a;color:#e0e0e0;border:1px solid #444;
    font-family:'Courier New',monospace;font-size:.9rem;}
  .stDownloadButton>button{background:#222;color:#ff6600;border:1px solid #ff6600;
    border-radius:4px;font-weight:600;}
  .stDownloadButton>button:hover{background:#ff6600;color:#000;}
  .status-box{background:#222;border-left:3px solid #ff6600;padding:10px 16px;
    border-radius:4px;margin:12px 0;font-size:.9rem;color:#aaa;}
  .detected-lang{background:#1a1a2e;border-left:3px solid #4466ff;padding:8px 14px;
    border-radius:4px;margin:8px 0;font-size:.85rem;color:#aab4ff;font-family:monospace;}
  .stExpander{border:1px solid #2a2a2a !important;border-radius:6px !important;}

  /* ── Hide Streamlit chrome ── */
  header[data-testid="stHeader"]    { display:none !important; }
  [data-testid="stToolbar"]         { display:none !important; }
  [data-testid="stDecoration"]      { display:none !important; }
  [data-testid="stStatusWidget"]    { display:none !important; }
  [data-testid="stMainMenuPopover"] { display:none !important; }
  #MainMenu                         { display:none !important; }
  footer                            { display:none !important; }
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────
for key, default in [
    ("transcript_text", ""),
    ("admin_ok",        False),
    ("tts_open",        False),
    ("tts_input",       ""),
    ("download_filename","transkript.txt"),
    ("detected_lang",   ""),
    ("trl_result",      ""),
    ("trl_open",        False),
    ("copy_triggered",   False),
    ("subtitle_segments", []),
    ("trl_segments",     []),
    ("fps",                25),
    ("ext_lang_choice",   ""),
    ("_cached_file_bytes", b""),
    ("_cached_file_name",  ""),
    ("_cached_file_size",   0),
    ("_last_lang_choice",  ""),
    ("_last_timecode",     False),
    ("_tx_version",        0),
    ("tts_chunks",         []),
    ("tts_chunk_voice",    ""),
    ("tts_show_uploader",  False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ════════════════════════════════════════════════════════════════════════════
# LANGUAGE MAPS
# ════════════════════════════════════════════════════════════════════════════
LANGUAGE_MAP = {"Hrvatski":"hr","English":"en","Italiano":"it","Deutsch":"de","Français":"fr"}
CODE_TO_LABEL = {v: k for k, v in LANGUAGE_MAP.items()}

# Full language list for advanced use
EXTENDED_LANGUAGE_MAP = {
    "Afrikaans":"af","Albanian":"sq","Amharic":"am","Arabic":"ar",
    "Armenian":"hy","Azerbaijani":"az","Basque":"eu","Bengali":"bn",
    "Bosnian":"bs","Bulgarian":"bg","Catalan":"ca","Chinese (Simplified)":"zh",
    "Chinese (Traditional)":"zh-TW","Croatian":"hr","Czech":"cs","Danish":"da",
    "Dutch":"nl","English":"en","Estonian":"et","Finnish":"fi","French":"fr",
    "Galician":"gl","German":"de","Greek":"el","Gujarati":"gu","Hebrew":"he",
    "Hindi":"hi","Hungarian":"hu","Icelandic":"is","Indonesian":"id",
    "Italian":"it","Japanese":"ja","Kannada":"kn","Kazakh":"kk","Korean":"ko",
    "Latvian":"lv","Lithuanian":"lt","Macedonian":"mk","Malay":"ms",
    "Maltese":"mt","Marathi":"mr","Mongolian":"mn","Nepali":"ne",
    "Norwegian":"no","Pashto":"ps","Persian":"fa","Polish":"pl",
    "Portuguese":"pt","Punjabi":"pa","Romanian":"ro","Russian":"ru",
    "Serbian":"sr","Slovak":"sk","Slovenian":"sl","Somali":"so",
    "Spanish":"es","Swahili":"sw","Swedish":"sv","Tagalog":"tl",
    "Tamil":"ta","Telugu":"te","Thai":"th","Turkish":"tr","Ukrainian":"uk",
    "Urdu":"ur","Uzbek":"uz","Vietnamese":"vi","Welsh":"cy","Zulu":"zu",
}

# ════════════════════════════════════════════════════════════════════════════
# WHAT THIS SESSION HAS COST
# ════════════════════════════════════════════════════════════════════════════
#
# THE RUNNING TOTAL WAS A GUESS DRESSED AS A BALANCE, and it is worth saying exactly how.
#
# It summed `duration_sec` over every row of the Google Sheet, multiplied by a rate typed into
# the source, and subtracted that from a credit figure — 50.00 — also typed into the source. It
# then printed "$x remaining" in the colour of a fuel gauge. Nothing in it had ever spoken to
# AssemblyAI. If the real balance drifted from the guess, and it did, there was a box to type
# the true number into so the guess could be corrected by hand.
#
# WITH THE SHEET GONE THERE IS NO HISTORY TO SUM, so that bar would read "full" forever. A gauge
# that always reads full is worse than no gauge: it is read at a glance and believed.
#
# What replaces it is the one number this app can state as a fact — how many seconds it has sent
# to AssemblyAI SINCE IT LOADED, and what that cost at the published rate. It is smaller than
# what it replaces, and unlike what it replaces it is true. The real balance lives in the
# AssemblyAI dashboard, which is linked rather than guessed at.
RATE_PER_HOUR = 0.15
st.session_state.setdefault("seconds_sent", 0)

# ════════════════════════════════════════════════════════════════════════════
# RECORDER HTML
# ════════════════════════════════════════════════════════════════════════════
RECORDER_HTML = """
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:8px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="color:#ff6600;font-size:11px;letter-spacing:3px;font-family:monospace;">AUDIO MONITOR</span>
    <span id="timer" style="color:#ff6600;font-size:24px;font-weight:700;font-family:monospace;letter-spacing:3px;">00:00</span>
    <span id="recDot" style="color:#444;font-size:11px;font-family:monospace;">● STANDBY</span>
  </div>
  <canvas id="waveCanvas" height="60"
    style="width:100%;height:60px;background:#0a0a0a;border-radius:4px;display:block;margin-bottom:10px;"></canvas>
  <div style="display:flex;gap:10px;margin-bottom:12px;">
    <button id="btnStart" onclick="startRec()"
      style="flex:1;background:#ff6600;color:#000;border:none;border-radius:4px;
             padding:12px;font-weight:700;font-size:13px;cursor:pointer;">⏺ REC</button>
    <button id="btnStop" onclick="stopRec()" disabled
      style="flex:1;background:#333;color:#666;border:1px solid #444;border-radius:4px;
             padding:12px;font-weight:700;font-size:13px;cursor:not-allowed;">■ STOP</button>
  </div>
  <div style="margin-bottom:10px;">
    <div style="font-family:monospace;font-size:10px;color:#444;margin-bottom:4px;letter-spacing:2px;">INPUT LEVEL</div>
    <div style="background:#0a0a0a;border-radius:3px;height:8px;overflow:hidden;">
      <div id="levelFill" style="height:100%;width:0%;background:#ff6600;border-radius:3px;transition:width 0.1s;"></div>
    </div>
  </div>
  <div id="statusMsg" style="font-family:monospace;font-size:11px;color:#555;margin-bottom:12px;">initializing microphone...</div>
  <div id="downloadWrap" style="display:none;">
    <audio id="audioPlayer" controls style="width:100%;margin-bottom:10px;filter:invert(0.8) hue-rotate(180deg);"></audio>
    <a id="downloadBtn" style="display:block;background:#ff6600;color:#000;text-align:center;
       padding:12px;border-radius:4px;font-weight:700;font-size:13px;text-decoration:none;cursor:pointer;">
      ⬇ SPREMI NA DISK</a>
    <div style="margin-top:8px;padding:8px 12px;background:#1a2a1a;border-left:3px solid #44cc88;
                border-radius:4px;font-size:11px;color:#44cc88;font-family:monospace;">
      ✓ Spremi datoteku — zatim je uploadaj ispod</div>
  </div>
</div>
<script>
const canvas=document.getElementById('waveCanvas'),ctx=canvas.getContext('2d');
const timerEl=document.getElementById('timer'),recDot=document.getElementById('recDot');
const levelFl=document.getElementById('levelFill'),statusEl=document.getElementById('statusMsg');
let analyser,dataArray,mediaRecorder,chunks=[],timerInt=null,seconds=0,isRec=false,stream=null,recCount=0;
function pad(n){return String(n).padStart(2,'0');}
function drawLoop(){
  requestAnimationFrame(drawLoop);
  const W=canvas.offsetWidth*(window.devicePixelRatio||1),H=60*(window.devicePixelRatio||1);
  if(canvas.width!==W)canvas.width=W;canvas.height=H;
  ctx.fillStyle='#0a0a0a';ctx.fillRect(0,0,W,H);
  if(!analyser)return;
  analyser.getByteTimeDomainData(dataArray);
  ctx.lineWidth=isRec?2:1;ctx.strokeStyle=isRec?'#ff6600':'#444';
  ctx.shadowBlur=isRec?12:0;ctx.shadowColor='#ff6600';ctx.beginPath();
  const sw=W/dataArray.length;
  for(let i=0;i<dataArray.length;i++){const y=((dataArray[i]/128)-1)*(H/2)+H/2;i===0?ctx.moveTo(0,y):ctx.lineTo(i*sw,y);}
  ctx.stroke();ctx.shadowBlur=0;
  let sum=0;for(let i=0;i<dataArray.length;i++)sum+=Math.abs(dataArray[i]-128);
  const lvl=Math.min(100,(sum/dataArray.length)*4);
  levelFl.style.width=lvl+'%';
  levelFl.style.background=lvl>70?'#ff4444':lvl>40?'#ffaa00':'#ff6600';
}
async function initMic(){
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
    const actx=new(window.AudioContext||window.webkitAudioContext)();
    const source=actx.createMediaStreamSource(stream);
    analyser=actx.createAnalyser();analyser.fftSize=2048;
    dataArray=new Uint8Array(analyser.frequencyBinCount);source.connect(analyser);
    statusEl.textContent='Mikrofon spreman — pritisni REC';statusEl.style.color='#ff6600';
  }catch(e){statusEl.textContent='Mikrofon nedostupan: '+e.message;statusEl.style.color='#ff4444';}
}
function startRec(){
  if(!stream){statusEl.textContent='Nema mikrofona!';return;}
  chunks=[];document.getElementById('downloadWrap').style.display='none';
  const formats=['audio/mp4;codecs=aac','audio/mp4','audio/aac','audio/ogg;codecs=opus','audio/webm;codecs=opus','audio/webm'];
  const mimeType=formats.find(f=>MediaRecorder.isTypeSupported(f))||'';
  mediaRecorder=new MediaRecorder(stream,mimeType?{mimeType}:{});
  mediaRecorder.ondataavailable=e=>{if(e.data.size>0)chunks.push(e.data);};
  mediaRecorder.onstop=buildDownload;mediaRecorder.start(100);
  isRec=true;seconds=0;timerEl.textContent='00:00';timerEl.style.color='#ff4444';
  recDot.textContent='● REC';recDot.style.color='#ff4444';
  statusEl.textContent='Snimanje u tijeku...';statusEl.style.color='#ff4444';
  document.getElementById('btnStart').disabled=true;
  document.getElementById('btnStart').style.background='#552200';
  document.getElementById('btnStart').style.color='#888';
  document.getElementById('btnStop').disabled=false;
  document.getElementById('btnStop').style.background='#ff4444';
  document.getElementById('btnStop').style.color='#fff';
  document.getElementById('btnStop').style.cursor='pointer';
  timerInt=setInterval(()=>{seconds++;timerEl.textContent=pad(Math.floor(seconds/60))+':'+pad(seconds%60);},1000);
}
function stopRec(){
  if(mediaRecorder&&mediaRecorder.state!=='inactive')mediaRecorder.stop();
  clearInterval(timerInt);isRec=false;
  recDot.textContent='■ DONE';recDot.style.color='#44cc88';timerEl.style.color='#44cc88';
  statusEl.textContent='Snimanje završeno';statusEl.style.color='#44cc88';
  document.getElementById('btnStart').disabled=false;
  document.getElementById('btnStart').style.background='#ff6600';
  document.getElementById('btnStart').style.color='#000';
  document.getElementById('btnStop').disabled=true;
  document.getElementById('btnStop').style.background='#333';
  document.getElementById('btnStop').style.color='#666';
  document.getElementById('btnStop').style.cursor='not-allowed';
}
function buildDownload(){
  recCount++;
  const blob=new Blob(chunks,{type:mediaRecorder.mimeType||'audio/webm'});
  const url=URL.createObjectURL(blob);
  const mt=mediaRecorder.mimeType||'';
  const ext=mt.includes('mp4')||mt.includes('aac')?'m4a':mt.includes('ogg')?'ogg':'webm';
  const name='snimka_'+pad(recCount)+'.'+ext;
  document.getElementById('audioPlayer').src=url;
  const dlBtn=document.getElementById('downloadBtn');
  dlBtn.href=url;dlBtn.download=name;dlBtn.textContent='⬇ SPREMI NA DISK ('+name+')';
  document.getElementById('downloadWrap').style.display='block';
}
drawLoop();window.addEventListener('load',initMic);
</script>"""

# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════
def ms_to_tc(ms):
    total_s = ms // 1000
    return f"{total_s//3600:02d}:{(total_s%3600)//60:02d}:{total_s%60:02d}.{(ms%1000)//10:02d}"


def speaker_text(utterances, names, with_timecode=False):
    """The transcript as speech, with whatever he has called each voice.

    AssemblyAI hands back letters — A, B, C — and a letter is not a person.
    This is where a letter becomes "Marinko" or "the mayor", and the
    transcript is rebuilt from the utterances every time a name changes, so
    renaming somebody in the middle of reading costs nothing.
    """
    lines = []
    for u in utterances:
        who = u.get("speaker", "?")
        # SQUARE BRACKETS, AND THE LETTER ON ITS OWN. Baba, 17.9.2026:
        # "each speaker label will be inside square brackets."
        #
        # [A] rather than "Speaker A:" — shorter on a phone, and it reads
        # as a MARK rather than as something somebody said. In a broadcast
        # script a bracket is already what a name in the margin looks
        # like, and when he names the voice it simply becomes [Marinko].
        label = (names.get(who) or "").strip() or str(who)
        said = (u.get("text") or "").strip()
        if not said:
            continue
        if with_timecode:
            # THE TIMECODE KEEPS ITS OWN BRACKETS and comes first, so two
            # bracketed things in a row never read as one.
            lines.append("[%s] [%s] %s"
                         % (ms_to_tc(u.get("start", 0)), label, said))
        else:
            lines.append("[%s] %s" % (label, said))
    return "\n\n".join(lines)


def speakers_heard(utterances):
    """Every voice in the recording, in the order it was first heard."""
    seen = []
    for u in utterances:
        who = u.get("speaker")
        if who and who not in seen:
            seen.append(who)
    return seen


# upload_with_progress() STOOD HERE, 18 lines, and nothing called it. It streamed the file to
# AssemblyAI in 32 KB chunks behind a progress bar with a KB/s readout. transcribe()'s own
# docstring already says what happened to it: "engine.run_transcription replaces all of it —
# ffmpeg straight to Opus in ten-minute pieces, six at a time, each piece on ONE key with the
# ring behind it". The replacement landed; the original was never taken out.

def transcribe(audio_bytes, filename="audio", lang_choice="Auto detect",
               include_timecode=False, speakers=False, speaker_count=0):
    """The whole job, shown as it happens.

    THE OLD VERSION DID ONE UPLOAD AND ONE POLL LOOP on a single hard-coded
    key, converted stereo to mono as an MP3, and showed a bare "Processing…
    (33s)" counter that could not say whether anything was wrong. It also
    kept the entire file in memory as WAV.

    engine.run_transcription replaces all of it: ffmpeg straight to Opus in
    ten-minute pieces, six at a time, each piece on ONE key with the ring
    behind it, and the text box filling as the pieces land.
    """
    if lang_choice == "Hrvatski":
        lang_params = {"language_code": "hr"}
    elif lang_choice == "English":
        lang_params = {"language_code": "en"}
    else:
        lang_params = {"language_detection": True}

    status_box = st.empty()
    text_box = st.empty()
    # THE VERBOSE MONITOR IS FOLDED AWAY, not absent. Baba asked for it, and
    # somebody who is not debugging should not have to read it — but when a
    # transcription goes wrong it is the only thing that says why.
    monitor = st.expander("what is happening", expanded=False)
    notes = []

    class _UI:
        def status(self, t):
            status_box.markdown(
                "<div style='font-family:monospace;font-size:13px;"
                "color:#ff9d3c;padding:4px 0'>%s</div>" % t,
                unsafe_allow_html=True)

        def text(self, t):
            text_box.text_area("transcript so far", value=t, height=260,
                               key="_live_%d" % len(t), disabled=True)

        def note(self, t):
            notes.append("%s  %s" % (time.strftime("%H:%M:%S"), t))
            with monitor:
                st.markdown("<div style='font-family:monospace;font-size:11px;"
                            "color:#888'>%s</div>" % notes[-1],
                            unsafe_allow_html=True)

    try:
        result_text, utterances = engine_run(
            audio_bytes, filename, lang_params, _UI(),
            speakers=(speaker_count or True) if speakers else None)
    except Exception as exc:                                 # noqa: BLE001
        # A FAILURE IS A SENTENCE, NEVER SILENCE. The worst bug this app
        # family ever had was a control that did nothing at all.
        status_box.empty()
        st.error(str(exc))
        st.stop()

    text_box.empty()
    # THE LABELS ARE THERE FROM THE FIRST LOOK. Baba: "automatically add
    # speaker labels, speaker A, B, C, D. If user change it and apply,
    # then you change also by the real name."
    #
    # Until now the transcript arrived as one block of prose and the
    # letters only appeared after somebody opened the panel, typed a name
    # and pressed apply — so the diarisation he had waited longer for was
    # invisible unless he went looking for it.
    if utterances and len(speakers_heard(utterances)) > 1:
        result_text = speaker_text(
            utterances, st.session_state.get("_speaker_names", {}),
            st.session_state.get("_include_timecode", False))
    if not result_text.strip():
        st.warning("Nothing was recognised in that audio.")
    poll = {"text": result_text, "language_code": lang_params.get(
        "language_code", "")}
    st.session_state["_utterances"] = utterances

    detected_code  = poll.get("language_code", "")
    detected_label = CODE_TO_LABEL.get(detected_code, detected_code.upper())

    if include_timecode and poll.get("words"):
        words = poll["words"]; lines, cur, cs = [], [], words[0]["start"]
        for i, w in enumerate(words):
            cur.append(w["text"])
            if len(cur) >= 10 or i == len(words) - 1:
                lines.append(f"[{ms_to_tc(cs)}]  {' '.join(cur)}")
                cur = []
                if i < len(words) - 1:
                    cs = words[i+1]["start"]
        result_text = "\n\n".join(lines)
    else:
        result_text = poll.get("text", "")

    duration_sec = int(poll.get("audio_duration", 0))

    # THE ONLY THING RECORDED ABOUT A RUN, and it is about the ACCOUNT rather than the person:
    # how many seconds were sent, so the tab can say what this session cost. It lives in session
    # state, so it is gone when the tab is closed and it never leaves the browser's session.
    #
    # What used to be here instead built a fourteen-column row — filename, the first and last
    # three words of the transcript, the visitor's IP address, their city, country, ISP and
    # which broadcaster they appeared to work for — and appended it to a spreadsheet.
    st.session_state["seconds_sent"] = st.session_state.get("seconds_sent", 0) + duration_sec

    # Generate subtitle segments from word timestamps
    st.session_state.subtitle_segments = words_to_subtitles(poll.get("words", []))
    st.session_state["audio_duration_ms"] = int(poll.get("audio_duration", 0)) * 1000
    return result_text, duration_sec, detected_code, detected_label

def translate_text(text, from_code, to_code):
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={
            "q":        text[:5000],
            "langpair": f"{from_code}|{to_code}",
            "de":       "marko.bosko@auroville.community",
        },
        timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("responseStatus") != 200:
        raise Exception(data.get("responseDetails", "Translation failed"))
    return data["responseData"]["translatedText"]




# ════════════════════════════════════════════════════════════════════════════
# SUBTITLE HELPERS
# ════════════════════════════════════════════════════════════════════════════
def ms_to_srt_time(ms):
    ms = int(ms)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def ms_to_avid_time(ms, fps=25):
    total_f = round(int(ms) * fps / 1000)
    h, r = divmod(total_f, fps * 3600)
    m, r = divmod(r, fps * 60)
    s, f = divmod(r, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

def words_to_subtitles(words, max_chars=72, max_dur_ms=5500):
    if not words:
        return []
    segs, cur, cur_start, cur_chars = [], [], None, 0
    for w in words:
        txt, ws, we = w["text"], w["start"], w["end"]
        if cur_start is None:
            cur_start = ws
        new_chars = cur_chars + len(txt) + (1 if cur_chars else 0)
        if (new_chars > max_chars or we - cur_start > max_dur_ms) and cur:
            segs.append({"start": cur_start, "end": cur[-1]["end"],
                         "text": " ".join(x["text"] for x in cur)})
            cur, cur_start, cur_chars = [w], ws, len(txt)
        else:
            cur.append(w); cur_chars = new_chars
    if cur:
        segs.append({"start": cur_start, "end": cur[-1]["end"],
                     "text": " ".join(x["text"] for x in cur)})
    return segs

def subtitles_to_srt(segs):
    out = []
    for i, s in enumerate(segs, 1):
        out += [str(i),
                f"{ms_to_srt_time(s['start'])} --> {ms_to_srt_time(s['end'])}",
                s["text"], ""]
    return "\n".join(out)

def subtitles_to_avid(segs, fps=25):
    out = ["<begin subtitles>", ""]
    for s in segs:
        out.append(f"{ms_to_avid_time(s['start'], fps)} {ms_to_avid_time(s['end'], fps)}")
        text = s["text"]
        if len(text) > 40:
            mid = len(text) // 2
            sp  = text.rfind(" ", 0, mid + 15) or mid
            out += [text[:sp], text[sp+1:], ""]
        else:
            out += [text, ""]
    out.append("<end subtitles>")
    return "\n".join(out)

def translate_subtitle_segments(segs, from_code, to_code):
    result = []
    for s in segs:
        try:
            t = translate_text(s["text"], from_code, to_code)
        except:
            t = s["text"]
        result.append({"start": s["start"], "end": s["end"], "text": t})
    return result

def generate_timed_tts_audio(segs, voice, total_ms):
    """Generate dubbed audio: TTS per subtitle placed at original timestamps via ffmpeg."""
    if not segs:
        return None
    temp_segs, base_tmp, out_tmp = [], None, None
    try:
        for seg in segs:
            audio_data, _ = generate_tts(seg["text"], voice)
            f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            f.write(audio_data); f.close()
            temp_segs.append((f.name, int(seg["start"])))

        base_tmp = tempfile.mktemp(suffix=".mp3")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
            "-t", str(max(total_ms / 1000.0, 1)), "-q:a", "9", base_tmp
        ], capture_output=True, check=True)

        inputs = ["-i", base_tmp]
        for seg_file, _ in temp_segs:
            inputs += ["-i", seg_file]

        filt, mix = [], "[0:a]"
        for i, (_, start_ms) in enumerate(temp_segs):
            filt.append(f"[{i+1}:a]adelay={start_ms}|{start_ms}[d{i}]")
            mix += f"[d{i}]"
        n = len(temp_segs) + 1
        filt.append(f"{mix}amix=inputs={n}:duration=first:normalize=0[out]")

        out_tmp = tempfile.mktemp(suffix=".mp3")
        r = subprocess.run(
            ["ffmpeg", "-y"] + inputs +
            ["-filter_complex", ";".join(filt), "-map", "[out]", "-q:a", "4", out_tmp],
            capture_output=True)
        if r.returncode == 0:
            return open(out_tmp, "rb").read()
    except Exception as e:
        st.error(f"Timed TTS error: {e}")
    finally:
        for f, _ in temp_segs:
            try: os.unlink(f)
            except: pass
        for tmp in [base_tmp, out_tmp]:
            if tmp:
                try: os.unlink(tmp)
                except: pass
    return None


def strip_timecodes(text):
    """Remove [HH:MM:SS.ff] timecode markers so they are not spoken."""
    text = re.sub(r"\[\d{2}:\d{2}:\d{2}[.,]\d{2,3}\]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()

def split_text_chunks(text, max_chars=3000):
    """Split clean text into chunks under max_chars at sentence boundaries."""
    text = strip_timecodes(text)
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for sent in sentences:
        while len(sent) > max_chars:
            chunks.append(sent[:max_chars]); sent = sent[max_chars:]
        if len(cur) + len(sent) + 1 > max_chars and cur:
            chunks.append(cur.strip()); cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        chunks.append(cur.strip())
    return chunks

def join_audio_chunks(audio_list):
    """Concatenate multiple MP3 byte blobs into one via ffmpeg concat."""
    if not audio_list:
        return None
    temp_files, list_file, out_file = [], None, None
    try:
        for audio in audio_list:
            f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            f.write(audio); f.close()
            temp_files.append(f.name)
        list_file = tempfile.mktemp(suffix=".txt")
        with open(list_file, "w") as lf:
            for tf in temp_files:
                lf.write("file '" + tf + "'\n")
        out_file = tempfile.mktemp(suffix=".mp3")
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", out_file], capture_output=True)
        if r.returncode != 0:
            r = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", list_file, "-q:a", "4", out_file], capture_output=True)
        if r.returncode == 0:
            return open(out_file, "rb").read()
    finally:
        for tf in temp_files:
            try: os.unlink(tf)
            except: pass
        for tmp in [list_file, out_file]:
            if tmp:
                try: os.unlink(tmp)
                except: pass
    return None

# ════════════════════════════════════════════════════════════════════════════
# 3-TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["Transcript", "Translation", "TTS", "CRE"])


# ─────────────────────────────────────────────────────
# TAB 1 — TRANSCRIPT
# ─────────────────────────────────────────────────────
with tab1:
    has_transcript = bool(st.session_state.transcript_text)
    has_cache      = bool(st.session_state.get("_cached_file_name"))

    # ─── SHARED ACTION: run or re-run transcription ───────────────────────────
    def run_transcription():
        _lc    = st.session_state.get("_lang_choice", "Auto detect")
        _tc    = st.session_state.get("_include_timecode", False)
        _bytes = st.session_state["_cached_file_bytes"]
        _name  = st.session_state["_cached_file_name"]
        try:
            _sp = st.session_state.get("_speakers", False)
            _spn = st.session_state.get("_speaker_count", 0)
            result_text, dur, det_code, det_label = transcribe(
                _bytes, _name, _lc, _tc, speakers=_sp, speaker_count=_spn)
            st.session_state.transcript_text   = result_text
            st.session_state.tts_input         = result_text
            st.session_state.detected_lang     = det_code
            st.session_state.trl_result        = ""
            st.session_state.trl_segments      = []
            base = os.path.splitext(_name)[0]
            tc_s = "_timecode" if _tc else ""
            st.session_state.download_filename = f"{base}_{det_code}{tc_s}.txt"
            # Track settings used for this transcription
            st.session_state["_last_lang_choice"] = _lc
            st.session_state["_last_timecode"]     = _tc
            # Bump version → text area gets a fresh widget key → reads new value
            st.session_state["_tx_version"] = st.session_state.get("_tx_version", 0) + 1
            st.rerun()
        except requests.exceptions.HTTPError as e:
            st.error(f"HTTP error: {e.response.status_code} — {e.response.text}")
        except Exception as e:
            st.error(f"Error: {str(e)}")

    # ─── WHO IS SPEAKING ──────────────────────────────────────────────────────
    #
    # One row per voice: how much it said, and a box to name it. Naming is
    # what makes a diarised transcript usable — "Speaker B" is no better than
    # a letter when he is cutting an interview at midnight.
    # ─── POST-TRANSCRIPT VIEW ─────────────────────────────────────────────────
    if has_transcript:
        det_code  = st.session_state.detected_lang
        det_label = CODE_TO_LABEL.get(det_code, det_code.upper()) if det_code else ""
        segs      = st.session_state.subtitle_segments
        fps       = st.session_state.fps
        total_ms  = st.session_state.get("audio_duration_ms", 0)

        # ROW 1: Download | Copy | New
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button("Download", data=st.session_state.transcript_text.encode(),
                file_name=st.session_state.download_filename, mime="text/plain",
                use_container_width=True, key="dl_top")
        with c2:
            _ch = f"""<style>body{{margin:0;background:transparent;}}
.cp{{background:#ff6600;color:#000;font-weight:700;border:none;border-radius:4px;
     padding:7px 0;font-size:.85rem;letter-spacing:.5px;text-transform:uppercase;
     cursor:pointer;width:100%;display:block;}}.cp:active{{background:#cc5200;}}
#msg{{color:#44cc88;font-family:monospace;font-size:11px;padding:2px 0;display:none;text-align:center;}}
</style><button class="cp" onclick="doCopy()">COPY</button><div id="msg">✓ Copied</div>
<script>function doCopy(){{var t={json.dumps(st.session_state.transcript_text)};var ok=false;
try{{var a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');
a.style.cssText='position:absolute;left:-9999px;top:0;opacity:0;';document.body.appendChild(a);
if(/ipad|iphone/i.test(navigator.userAgent)){{var rng=document.createRange();rng.selectNodeContents(a);
var sel=window.getSelection();sel.removeAllRanges();sel.addRange(rng);a.setSelectionRange(0,999999);}}
else{{a.select();}}ok=document.execCommand('copy');document.body.removeChild(a);}}catch(e){{}}
if(!ok&&navigator.clipboard)navigator.clipboard.writeText(t).catch(function(){{}});
var m=document.getElementById('msg');m.style.display='block';
setTimeout(function(){{m.style.display='none';}},2000);}}</script>"""
            st.components.v1.html(_ch, height=52)
        with c3:
            if st.button("New", use_container_width=True, key="new_session"):
                # NEW MEANS NEW. Baba, 17.9.2026: "when I click New
                # Transcription, the speakers are surviving. New
                # transcription should delete everything."
                #
                # THE LIST WAS HAND-WRITTEN, so it held whatever existed on
                # the day somebody wrote it and knew nothing of the speaker
                # work added afterwards. The old voices stayed, their names
                # stayed, and the name boxes kept their typed values —
                # which is worse than untidy: the next recording would be
                # offered "Marinko" for a voice that is not his.
                #
                # A hand-written list of things to clear will be wrong
                # again the next time something is added. The speaker keys
                # are cleared BY PREFIX so anything named that way goes
                # with them.
                for k in ["transcript_text","detected_lang","trl_result","tts_input",
                          "subtitle_segments","trl_segments","audio_duration_ms",
                          "_cached_file_name","_cached_file_bytes"]:
                    st.session_state[k] = (
                        [] if k in ["subtitle_segments","trl_segments"]
                        else b"" if k == "_cached_file_bytes"
                        else "")
                for k in ["_utterances", "_speaker_names"]:
                    st.session_state.pop(k, None)
                # The per-voice name boxes are widgets, so their typed text
                # lives under their own keys and outlives the dictionary.
                for k in [x for x in list(st.session_state)
                          if str(x).startswith("_spname_")]:
                    st.session_state.pop(k, None)
                st.session_state.download_filename = "transkript.txt"
                st.rerun()

        # ROW 2: SRT | Avid
        if segs:
            c4, c5, c6 = st.columns(3)
            with c4:
                fps_val = st.selectbox("FPS", [23, 24, 25, 30], index=2,
                                       key="fps_sel", label_visibility="collapsed")
                st.session_state.fps = fps_val
            with c5:
                st.download_button("SRT",
                    data=subtitles_to_srt(segs).encode("utf-8"),
                    file_name=f"{st.session_state.download_filename.replace('.txt','')}.srt",
                    mime="text/plain", use_container_width=True, key="dl_srt")
            with c6:
                st.download_button("Avid",
                    data=subtitles_to_avid(segs, fps_val).encode("utf-8"),
                    file_name=f"{st.session_state.download_filename.replace('.txt','')}.txt",
                    mime="text/plain", use_container_width=True, key="dl_avid")

        # WHAT THIS RECORDING TURNED OUT TO BE — and it does not claim to
        # have DETECTED a language he chose himself.
        #
        # Baba, 17.9.2026: "detected Croatian language. It is not detected.
        # The user pressed Croatian and then detection is not needed. And
        # in the same status line put how many speakers are detected."
        #
        # He is right that the word was a lie. The app asks the language
        # before it starts, and if he answered "Hrvatski" then nothing was
        # detected — it was obeyed. The word is kept ONLY for Auto detect,
        # where it is true and worth knowing.
        _bits = []
        if det_code:
            _chose = st.session_state.get("_lang_choice", "Auto detect")
            _word = "Detected" if _chose == "Auto detect" else "Language"
            _bits.append("%s: <strong>%s</strong> &nbsp;·&nbsp; <code>%s</code>"
                         % (_word, det_label, det_code))
        _n_spk = len(speakers_heard(st.session_state.get("_utterances") or []))
        if _n_spk:
            _bits.append("Speakers: <strong>%d</strong>" % _n_spk)
        if _bits:
            st.markdown('<div class="detected-lang">%s</div>'
                        % " &nbsp;&nbsp;|&nbsp;&nbsp; ".join(_bits),
                        unsafe_allow_html=True)

    _utts = st.session_state.get("_utterances") or []
    if _utts:
        heard = speakers_heard(_utts)
        # FOLDED AWAY, NOT ABSENT. Baba: "speaker should come under a
        # speaker kind of title which user can uncollapse and add speakers.
        # Otherwise those speakers take a lot of real estate."
        #
        # Thirteen voices is thirteen rows of three columns between him and
        # his transcript, and most of the time he wants to read the words
        # rather than name anybody. The title carries the COUNT so the
        # panel says what is inside it without being opened.
        with st.expander("Speakers (%d) — name them and the transcript follows"
                         % len(heard), expanded=False):
            names = st.session_state.setdefault("_speaker_names", {})
            for who in heard:
                mine = [u for u in _utts if u.get("speaker") == who]
                said = sum(len((u.get("text") or "").split()) for u in mine)
                first = ms_to_tc(mine[0].get("start", 0)) if mine else "00:00:00.00"
                cols = st.columns([1, 2, 3])
                with cols[0]:
                    st.markdown("**%s**" % who)
                with cols[1]:
                    st.caption("%d words · first at %s" % (said, first))
                with cols[2]:
                    names[who] = st.text_input(
                        "name for %s" % who, value=names.get(who, ""),
                        key="_spname_%s" % who, label_visibility="collapsed",
                        placeholder="Speaker %s" % who)
            rebuilt = speaker_text(_utts, names,
                                   st.session_state.get("_include_timecode", False))
            if rebuilt and rebuilt != st.session_state.get("transcript_text", ""):
                if st.button("apply the names to the transcript"):
                    st.session_state.transcript_text = rebuilt
                    st.session_state.tts_input = rebuilt
                    st.session_state["_tx_version"] = st.session_state.get("_tx_version", 0) + 1
                    st.rerun()


        st.text_area("", st.session_state.transcript_text, height=360,
                     label_visibility="collapsed",
                     key=f"result_area_{st.session_state.get('_tx_version', 0)}")

    # ─── UPLOAD VIEW (no transcript yet) ─────────────────────────────────────
    else:
        # on_change callback fires the instant a file is selected — captures bytes
        # immediately and reliably on Android, no dependence on rerun timing.
        def _cache_uploaded_file():
            f = st.session_state.get("file_uploader_widget")
            if f is not None:
                st.session_state["_cached_file_bytes"] = f.getvalue()
                st.session_state["_cached_file_name"]  = f.name
                st.session_state["_cached_file_size"]  = f.size

        st.file_uploader(
            "",
            # ANY FILE FFMPEG CAN READ. Baba: "this file picker can accept
            # any file which FFmpeg can convert to audio. So it can be also
            # video file."
            #
            # A HAND-WRITTEN LIST IS A LIST THAT MISSES SOMETHING, and the
            # miss is silent: Streamlit simply refuses the file with no
            # explanation, and the person concludes their recording is
            # broken. ffmpeg reads hundreds of containers; these are the
            # ones a broadcast editor actually hands it, and the widened
            # tail is what an Android recorder and a camera produce.
            #
            # If something still gets refused, ffmpeg is the thing that
            # decides — not this list — so the error names ffmpeg.
            type=["mp3","mp4","m4a","wav","aac","ogg","flac","webm",
                  "mov","mxf","wma","opus","3gp","amr","mp2","mpga","mpeg",
                  "mkv","avi","wmv","flv","m4v","mts","m2ts","ts","vob",
                  "mpg","mp4v","caf","aiff","aif","aifc","oga","opus",
                  "wv","ape","dts","ac3","m4b","mka","f4v","asf","dv",
                  "r3d","braw","avchd","m2v","rm","au","snd","voc"],
            label_visibility="collapsed",
            key="file_uploader_widget",
            on_change=_cache_uploaded_file)

        # Reflect cache state set by the callback
        has_cache = bool(st.session_state.get("_cached_file_name"))

    # ─── SETTINGS, FOLDED AWAY ───────────────────────────────────────────────
    #
    # Baba, 17.9.2026: "settings should be collapsible because defaults are
    # good. All languages advanced should be in the section of languages."
    #
    # Five rows of radios stood between him and the one button he presses,
    # and on a phone that is most of a screen to scroll past every time.
    # The defaults are Croatian, no timecode, speakers detected — which is
    # what he wants nearly always.
    #
    # THE TITLE CARRIES THE CURRENT CHOICES, so the panel says what is
    # inside it without being opened. A folded panel that hides what it is
    # set to would turn every transcription into a guess about whether the
    # language is still right.
    _cur_lang = st.session_state.get("lang_radio", "Hrvatski")
    _cur_spk = st.session_state.get("sp_radio", "Detect")
    _cur_tc = st.session_state.get("tc_radio", "Off")
    _summary = "Settings — %s · speakers %s%s" % (
        _cur_lang, _cur_spk.lower(),
        " · timecode on" if _cur_tc == "On" else "")
    with st.expander(_summary, expanded=False):
        lang_choice = st.radio("Language", ["Hrvatski", "English", "Auto detect"],
                               horizontal=True, key="lang_radio")
        st.session_state["_lang_choice"] = lang_choice

        # THE LONG LIST BELONGS WITH THE LANGUAGE, and it was sitting under
        # the Transcribe button. That was not only untidy: it ran AFTER the
        # transcription on the same pass, so a language chosen here only
        # took effect on the NEXT press. Whoever used it once and got
        # Croatian anyway would have blamed the app and been right.
        with st.expander("All languages (advanced)", expanded=False):
            ext_lang = st.selectbox(
                "Select any language for transcription",
                ["— use primary selector above —"]
                + sorted(EXTENDED_LANGUAGE_MAP.keys()),
                key="ext_lang_sel")
            if ext_lang != "— use primary selector above —":
                st.session_state["_lang_choice"] = ext_lang
                st.info("Set to: %s (%s)"
                        % (ext_lang, EXTENDED_LANGUAGE_MAP.get(ext_lang, "")))

        tc_opt = st.radio("Timecode", ["Off", "On"], horizontal=True, key="tc_radio")
        st.session_state["_include_timecode"] = tc_opt == "On"

        # WHO IS SPEAKING (17.9.2026). Off by default, because it costs speed:
        # the whole recording has to go as one job for the labels to mean the
        # same thing from beginning to end, which gives up the six-way parallel
        # that makes this app quick.
        # DETECT FIRST, SO IT IS THE DEFAULT. Baba, 17.9.2026: "speaker
        # detection is default. First comes Detect and then second is Off."
        #
        # A radio takes its first option unless told otherwise, so the order IS
        # the default — there is no separate setting to keep in step with it.
        # Most of what goes through this app is an interview or a piece with
        # several voices, and the one case that does not need it, a voiceover
        # read by one person, costs nothing: one speaker is found and the
        # labels are dropped on the way out.
        sp_opt = st.radio("Speakers", ["Detect", "Off"], horizontal=True,
                          key="sp_radio")
        st.session_state["_speakers"] = sp_opt == "Detect"
        if sp_opt == "Detect":
            how_many = st.radio(
                "How many voices", ["I don't know", "2", "3", "4", "5", "6"],
                horizontal=True, key="sp_count")
            # A NUMBER IS A HARD BOUNDARY, NOT A HINT: their model merges extra
            # people into the labels it is allowed, so a wrong count is worse
            # than none. "I don't know" is the honest default.
            st.session_state["_speaker_count"] = (
                int(how_many) if how_many.isdigit() else 0)
            st.caption("Slower: the whole recording goes as one job so the "
                       "labels hold throughout. Each voice needs about half a "
                       "minute of speech to be recognised.")

    if not has_transcript:
        input_mode = st.radio("Source", ["Upload", "Rec"], horizontal=True)
        if input_mode == "Rec":
            st.components.v1.html(RECORDER_HTML, height=360)

    # ─── PRIMARY ACTION BUTTON ────────────────────────────────────────────────
    # Detects if settings differ from last transcription to label appropriately
    if has_cache:
        curr_lang = st.session_state.get("_lang_choice", "")
        curr_tc   = st.session_state.get("_include_timecode", False)
        last_lang = st.session_state.get("_last_lang_choice", "")
        last_tc   = st.session_state.get("_last_timecode", False)
        settings_changed = has_transcript and (curr_lang != last_lang or curr_tc != last_tc)

        if has_transcript:
            btn_label = "Re-Transcribe" + (" ↺" if settings_changed else "")
        else:
            btn_label = "Transcribe"

        if st.button(btn_label, use_container_width=True, key="do_transcribe"):
            run_transcription()



# ─────────────────────────────────────────────────────
# TAB 2 — TRANSLATION
# ─────────────────────────────────────────────────────
with tab2:
    det_code_trl  = st.session_state.get("detected_lang", "")
    source_is_hr  = det_code_trl == "hr"
    def_to_idx    = 1 if source_is_hr else 0
    from_code_trl = det_code_trl if det_code_trl in CODE_TO_LABEL else "en"

    trl_to = st.selectbox("Translate to", list(LANGUAGE_MAP.keys()),
                          index=def_to_idx, key="trl_to_sel")

    # Expanded target language
    with st.expander("More target languages", expanded=False):
        ext_trl_to = st.selectbox("Any language",
            ["— use above —"] + sorted(EXTENDED_LANGUAGE_MAP.keys()),
            key="ext_trl_to_sel")
        if ext_trl_to != "— use above —":
            trl_to = ext_trl_to

    ca, cb = st.columns(2)
    with ca:
        if st.button("Pull", use_container_width=True, key="trl_pull"):
            st.session_state["trl_input_area"] = st.session_state.transcript_text or ""
            st.session_state.trl_segments      = []
            st.rerun()
    with cb:
        do_translate = st.button("Translate", use_container_width=True, key="trl_btn")

    trl_input = st.text_area("", value=st.session_state.get("trl_input_area", ""),
                             height=180, key="trl_input_area",
                             label_visibility="collapsed",
                             placeholder="Pull from transcript or paste text here...")

    if do_translate:
        text_to_translate = trl_input.strip()
        if not text_to_translate:
            st.warning("No text to translate.")
        else:
            to_code = (EXTENDED_LANGUAGE_MAP.get(trl_to)
                       if trl_to in EXTENDED_LANGUAGE_MAP
                       else LANGUAGE_MAP.get(trl_to, "en"))
            if from_code_trl == to_code:
                st.warning("Source and target language are the same.")
            else:
                with st.spinner(f"Translating to {trl_to}..."):
                    try:
                        result = translate_text(text_to_translate, from_code_trl, to_code)
                        st.session_state.trl_result = result
                        # Also translate subtitle segments if available
                        src_segs = st.session_state.subtitle_segments
                        if src_segs:
                            with st.spinner("Translating subtitles..."):
                                st.session_state.trl_segments = translate_subtitle_segments(
                                    src_segs, from_code_trl, to_code)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Translation error: {e}")

    if st.session_state.trl_result:
        to_code_val = (EXTENDED_LANGUAGE_MAP.get(trl_to)
                       or LANGUAGE_MAP.get(trl_to, "xx"))
        fname_base  = f"translation_{from_code_trl}_{to_code_val}"
        fps_t       = st.session_state.fps

        st.text_area("", st.session_state.trl_result, height=180,
                     key="trl_result_area", label_visibility="collapsed")

        # Download row: text + SRT + Avid
        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button("Download", use_container_width=True,
                data=st.session_state.trl_result.encode("utf-8"),
                file_name=f"{fname_base}.txt", mime="text/plain", key="trl_dl_txt")
        trl_segs = st.session_state.trl_segments
        if trl_segs:
            with d2:
                st.download_button("SRT", use_container_width=True,
                    data=subtitles_to_srt(trl_segs).encode("utf-8"),
                    file_name=f"{fname_base}.srt", mime="text/plain", key="trl_dl_srt")
            with d3:
                st.download_button("Avid", use_container_width=True,
                    data=subtitles_to_avid(trl_segs, fps_t).encode("utf-8"),
                    file_name=f"{fname_base}_avid.txt", mime="text/plain", key="trl_dl_avid")


# ─────────────────────────────────────────────────────
# TAB 3 — TTS
# ─────────────────────────────────────────────────────
with tab3:
    det_code_tts = st.session_state.get("detected_lang", "")
    tts_detected = CODE_TO_LABEL.get(det_code_tts, "English") if det_code_tts else "English"
    tts_lang_idx = list(VOICE_MAP.keys()).index(tts_detected) if tts_detected in VOICE_MAP else 1

    # ── Three input sources: Pull transcript | Pull translation | Upload .txt ──
    cp1, cp2, cp3 = st.columns(3)
    with cp1:
        if st.button("Pull transcript", use_container_width=True, key="tts_pull_tra"):
            st.session_state["tts_text_area"] = st.session_state.transcript_text or ""
            st.rerun()
    with cp2:
        if st.button("Pull translation", use_container_width=True, key="tts_pull_trl"):
            st.session_state["tts_text_area"] = st.session_state.trl_result or ""
            st.rerun()
    with cp3:
        show_txt_upload = st.button("Upload .txt", use_container_width=True, key="tts_show_upload")
        if show_txt_upload:
            st.session_state["tts_show_uploader"] = not st.session_state.get("tts_show_uploader", False)

    # Text file uploader — on_change callback captures bytes immediately (Android fix)
    if st.session_state.get("tts_show_uploader", False):
        def _cache_txt_file():
            f = st.session_state.get("tts_txt_widget")
            if f is not None:
                try:
                    text = f.getvalue().decode("utf-8", errors="replace")
                    st.session_state["tts_text_area"] = text
                    st.session_state["tts_show_uploader"] = False
                except Exception:
                    pass

        st.file_uploader(
            "",
            type=["txt"],
            label_visibility="collapsed",
            key="tts_txt_widget",
            on_change=_cache_txt_file)

    tts_lang = st.radio("Language", list(VOICE_MAP.keys()), index=tts_lang_idx,
                        horizontal=True, key="tts_lang_sel")
    gender = st.radio("Voice", ["Female", "Male"], horizontal=True, key="tts_gender")
    gender_key     = "🚺 Female" if gender == "Female" else "🚹 Male"
    selected_voice = VOICE_MAP[tts_lang][gender_key]
    st.markdown(f'<div style="font-family:monospace;font-size:10px;color:#444;'
                f'margin-bottom:6px;">voice: {selected_voice}</div>',
                unsafe_allow_html=True)

    tts_text = st.text_area("", value=st.session_state.get("tts_text_area",""),
                            height=150, key="tts_text_area",
                            label_visibility="collapsed",
                            placeholder="Pull or paste text to read aloud...")

    timed_segs   = st.session_state.trl_segments or st.session_state.subtitle_segments
    total_ms     = st.session_state.get("audio_duration_ms", 0)
    source_label = "translated" if st.session_state.trl_segments else "original"

    # Both generate buttons side by side
    gb1, gb2 = st.columns(2)
    with gb1:
        do_std_tts = st.button("Generate", use_container_width=True, key="tts_btn")
    with gb2:
        timed_disabled = not bool(timed_segs)
        do_timed_tts   = st.button(
            "Timed audio" + ("" if timed_disabled else f" · {source_label}"),
            use_container_width=True, key="tts_timed_btn",
            disabled=timed_disabled,
            help="Transcribe first to enable" if timed_disabled else
                 f"{len(timed_segs)} segments · {total_ms//1000}s")

    if do_std_tts:
        clean = strip_timecodes(tts_text)
        if not clean:
            st.warning("No text.")
        else:
            chunks = split_text_chunks(clean, max_chars=3000)
            chunk_audios = []
            prog = st.progress(0.0, text=f"Generating {len(chunks)} chunk(s)...")
            try:
                for i, ch in enumerate(chunks):
                    audio_data, _ = generate_tts(ch, selected_voice)
                    if audio_data:
                        chunk_audios.append(audio_data)
                    prog.progress((i + 1) / len(chunks),
                                  text=f"Chunk {i+1}/{len(chunks)} done")
                prog.empty()
                st.session_state["tts_chunks"]      = chunk_audios
                st.session_state["tts_chunk_voice"] = f"{tts_lang}_{gender.lower()}"
            except Exception as exc:
                prog.empty()
                st.error(f"TTS error: {exc}")

    # ── Display generated chunks ──────────────────────────────────────────────
    chunk_audios = st.session_state.get("tts_chunks", [])
    if chunk_audios:
        safe = re.sub(r'[^a-z0-9]+','_', st.session_state.get("tts_chunk_voice","tts"))

        if len(chunk_audios) == 1:
            st.audio(chunk_audios[0], format="audio/mpeg")
            st.download_button("Download audio (MP3)", data=chunk_audios[0],
                file_name=f"tts_{safe}.mp3", mime="audio/mpeg",
                use_container_width=True, key="tts_dl_single")
        else:
            st.markdown(
                f'<div style="font-family:monospace;font-size:11px;color:#555;'
                f'margin:6px 0;">{len(chunk_audios)} chunks generated</div>',
                unsafe_allow_html=True)
            # JOIN ALL — top action
            if st.button(f"Join all {len(chunk_audios)} chunks → one file",
                         use_container_width=True, key="tts_join"):
                with st.spinner("Joining chunks with ffmpeg..."):
                    joined = join_audio_chunks(chunk_audios)
                    if joined:
                        st.success(f"Joined — {len(joined)//1024} KB")
                        st.download_button("Download full audio (MP3)", data=joined,
                            file_name=f"tts_{safe}_full.mp3", mime="audio/mpeg",
                            use_container_width=True, key="tts_dl_joined")
                    else:
                        st.error("Join failed.")
            # Individual chunk downloads
            for i, audio in enumerate(chunk_audios):
                st.download_button(f"Chunk {i+1} ({len(audio)//1024} KB)",
                    data=audio, file_name=f"tts_{safe}_part{i+1:02d}.mp3",
                    mime="audio/mpeg", use_container_width=True,
                    key=f"tts_chunk_dl_{i}")

    if do_timed_tts and timed_segs:
        with st.spinner(f"Generating {len(timed_segs)} segments and assembling..."):
            try:
                dub_audio = generate_timed_tts_audio(timed_segs, selected_voice, total_ms)
                if dub_audio:
                    st.success(f"Timed audio ready — {len(dub_audio)//1024} KB")
                    safe = re.sub(r'[^a-z0-9]+','_', tts_lang.lower())
                    st.download_button("Download dubbed audio (MP3)", data=dub_audio,
                        file_name=f"dubbed_{safe}_{gender.lower()}.mp3",
                        mime="audio/mpeg", key="tts_timed_dl")
                else:
                    st.error("Assembly failed.")
            except Exception as exc:
                st.error(f"Error: {exc}")


# ─────────────────────────────────────────────────────
# TAB 4 — CRE
# ─────────────────────────────────────────────────────
with tab4:
    sent      = int(st.session_state.get("seconds_sent", 0))
    spent_usd = sent / 3600.0 * RATE_PER_HOUR
    mins      = sent // 60
    secs      = sent % 60

    st.markdown(f"""
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:14px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="font-family:monospace;font-size:11px;color:#555;letter-spacing:2px;">
      THIS SESSION
    </span>
    <span style="font-family:monospace;font-size:15px;color:#ff6600;font-weight:700;">
      ${spent_usd:.3f}
    </span>
  </div>
  <div style="display:flex;justify-content:space-between;font-family:monospace;font-size:10px;color:#555;">
    <span>{mins}:{secs:02d} of audio sent</span>
    <span>AssemblyAI best model · ${RATE_PER_HOUR:.2f}/h</span>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown(
        '<div style="font-family:monospace;font-size:11px;color:#555;line-height:1.6;">'
        'This counts only what this browser tab has sent since it opened, at the published '
        'rate. It is not your balance and does not try to be.<br><br>'
        'The real figure is on the AssemblyAI dashboard, which is the only place that knows it:'
        '</div>',
        unsafe_allow_html=True)
    st.link_button("Open the AssemblyAI dashboard",
                   "https://www.assemblyai.com/app",
                   use_container_width=True)
