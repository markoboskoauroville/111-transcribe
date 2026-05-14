import streamlit as st
import requests
import time
import os

# ── API key ───────────────────────────────────────────────────────────────────
API_KEY = st.secrets["ASSEMBLYAI_API_KEY"]
HEADERS = {"authorization": API_KEY}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Marko Transcribe", page_icon="🎙️", layout="centered")
st.markdown('<div style="position:fixed;top:8px;left:12px;color:#666;font-size:12px;z-index:9999;font-family:monospace;">v1.4</div>', unsafe_allow_html=True)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&dis=swap');
  html, body, [class*="css"] { font-family: 'Inter', sans-serif; background-color: #1a1a1a; color: #e0e0e0; }
  .stApp { background-color: #1a1a1a; }
  h1 { color: #ff6600; font-weight: 700; letter-spacing: 1px; border-bottom: 2px solid #ff6600; padding-bottom: 8px; margin-bottom: 4px; }
  .subtitle { color: #888; font-size: 0.85rem; margin-bottom: 24px; letter-spacing: 2px; text-transform: uppercase; }
  .stRadio > label { color: #aaa; font-size: 0.8rem; letter-spacing: 1px; text-transform: uppercase; }
  .stRadio div[role="radiogroup"] label { color: #ccc; }
  .stButton > button { background-color: #ff6600; color: #000; font-weight: 700; border: none; border-radius: 4px; padding: 10px 28px; letter-spacing: 1px; text-transform: uppercase; }
  .stButton > button:hover { background-color: #cc5200; color: #fff; }
  .stTextArea textarea { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #444; font-family: 'Courier New', monospace; font-size: 0.9rem; }
  .stDownloadButton > button { background-color: #222; color: #ff6600; border: 1px solid #ff6600; border-radius: 4px; font-weight: 600; }
  .stDownloadButton > button:hover { background-color: #ff6600; color: #000; }
  .status-box { background-color: #222; border-left: 3px solid #ff6600; padding: 10px 16px; border-radius: 4px; margin: 12px 0; font-size: 0.9rem; color: #aaa; }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1>🎙️ MARKO TRANSCRIBE</h1>", unsafe_allow_html=True)
st.markdown('<div class="subtitle">Personal Transcription Tool</div>', unsafe_allow_html=True)

# ── Jezik ─────────────────────────────────────────────────────────────────────
LANGUAGE_MAP = {"Hrvatski": "hr", "English": "en", "Italiano": "it", "Deutsch": "de", "Français": "fr"}
lang_label = st.radio("JEZIK / LANGUAGE", list(LANGUAGE_MAP.keys()), horizontal=True)
lang_code  = LANGUAGE_MAP[lang_label]

# ── Timecode ──────────────────────────────────────────────────────────────────
timecode_option  = st.radio("TIMECODE U TEKSTU", ["Bez timecoda", "S timecodeom"], horizontal=True)
include_timecode = timecode_option == "S timecodeom"

st.markdown("---")

# ── Input mode ────────────────────────────────────────────────────────────────
input_mode = st.radio("IZVOR ZVUKA", ["📁 Upload datoteke", "🎤 Snimi + spremi + upload"], horizontal=True)

# ── Browser rekorder koji sprema na lokalni disk ──────────────────────────────
RECORDER_HTML = """
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:8px;">

  <div style="dis:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="color:#ff6600;font-size:11px;letter-spacing:3px;font-family:monospace;">AUDIO MONITOR</span>
    <span id="timer" style="color:#ff6600;font-size:24px;font-weight:700;font-family:monospace;letter-spacing:3px;">00:00</span>
    <span id="recDot" style="color:#444;font-size:11px;font-family:monospace;letter-spacing:1px;">● STANDBY</span>
  </div>

  <canvas id="waveCanvas" height="60"
    style="width:100%;height:60px;background:#0a0a0a;border-radius:4px;dis:block;margin-bottom:10px;"></canvas>

  <div style="dis:flex;gap:10px;margin-bottom:12px;">
    <button id="btnStart" onclick="startRec()"
      style="flex:1;background:#ff6600;color:#000;border:none;border-radius:4px;
             padding:12px;font-weight:700;font-size:13px;letter-spacing:1px;cursor:pointer;">
      ▶ REC
    </button>
    <button id="btnStop" onclick="stopRec()" disabled
      style="flex:1;background:#333;color:#666;border:1px solid #444;border-radius:4px;
             padding:12px;font-weight:700;font-size:13px;letter-spacing:1px;cursor:not-allowed;">
      ■ STOP
    </button>
  </div>

  <div id="levelWrap" style="margin-bottom:10px;">
    <div style="font-family:monospace;font-size:10px;color:#444;margin-bottom:4px;letter-spacing:2px;">INPUT LEVEL</div>
    <div style="background:#0a0a0a;border-radius:3px;height:8px;overflow:hidden;">
      <div id="levelFill" style="height:100%;width:0%;background:#ff6600;border-radius:3px;transition:width 0.1s;"></div>
    </div>
  </div>

  <div id="statusMsg" style="font-family:monospace;font-size:11px;color:#555;margin-bottom:12px;">
    initializing microphone...
  </div>

  <div id="downloadWrap" style="dis:none;">
    <audio id="audioer" controls
      style="width:100%;margin-bottom:10px;filter:invert(0.8) hue-rotate(180deg);"></audio>
    <a id="downloadBtn"
      style="dis:block;background:#ff6600;color:#000;text-align:center;padding:12px;
             border-radius:4px;font-weight:700;font-size:13px;letter-spacing:1px;
             text-decoration:none;cursor:pointer;">
      ⬇ SPREMI NA DISK (.wav)
    </a>
    <div style="margin-top:8px;padding:8px 12px;background:#1a2a1a;border-left:3px solid #44cc88;
                border-radius:4px;font-size:11px;color:#44cc88;font-family:monospace;">
      ✓ Nakon što spremiš datoteku — uploadaj je ispod pomoću file uploadera
    </div>
  </div>

</div>

<script>
const canvas  = document.getElementById('waveCanvas');
const ctx     = canvas.getContext('2d');
const timerEl = document.getElementById('timer');
const recDot  = document.getElementById('recDot');
const levelFl = document.getElementById('levelFill');
const statusEl= document.getElementById('statusMsg');

let analyser, dataArray, mediaRecorder;
let chunks = [];
let timerInt = null;
let seconds  = 0;
let isRec    = false;
let stream   = null;
let recCount = 0;

function pad(n){ return String(n).padStart(2,'0'); }

// ── Draw loop ─────────────────────────────────────────────────────────────────
function drawLoop() {
  requestAnimationFrame(drawLoop);

  const W = canvas.offsetWidth * (window.devicePixelRatio||1);
  const H = 60  * (window.devicePixelRatio||1);
  if (canvas.width !== W) canvas.width = W;
  canvas.height = H;

  ctx.fillStyle = '#0a0a0a';
  ctx.fillRect(0,0,W,H);

  // Centre grid line
  ctx.strokeStyle = '#1c1c1c';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0,H/2); ctx.lineTo(W,H/2); ctx.stroke();

  if (!analyser) {
    ctx.strokeStyle = '#222';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0,H/2); ctx.lineTo(W,H/2); ctx.stroke();
    return;
  }

  analyser.getByteTimeDomainData(dataArray);

  // Waveform
  ctx.lineWidth   = isRec ? 2 : 1;
  ctx.strokeStyle = isRec ? '#ff6600' : '#444';
  ctx.shadowBlur  = isRec ? 12 : 0;
  ctx.shadowColor = '#ff6600';
  ctx.beginPath();
  const sw = W / dataArray.length;
  for (let i=0; i<dataArray.length; i++) {
    const y = ((dataArray[i]/128)-1) * (H/2) + H/2;
    i===0 ? ctx.moveTo(0,y) : ctx.lineTo(i*sw,y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Level meter
  let sum=0;
  for (let i=0;i<dataArray.length;i++) sum+=Math.abs(dataArray[i]-128);
  const lvl = Math.min(100, (sum/dataArray.length)*4);
  levelFl.style.width = lvl + '%';
  levelFl.style.background = lvl > 70 ? '#ff4444' : lvl > 40 ? '#ffaa00' : '#ff6600';
}

// ── Init mic ──────────────────────────────────────────────────────────────────
async function initMic() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    const actx   = new (window.AudioContext||window.webkitAudioContext)();
    const source = actx.createMediaStreamSource(stream);
    analyser      = actx.createAnalyser();
    analyser.fftSize = 2048;
    dataArray     = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);
    statusEl.textContent  = 'Mikrofon spreman — pritisni START';
    statusEl.style.color  = '#ff6600';
  } catch(e) {
    statusEl.textContent = 'Mikrofon nedostupan: ' + e.message;
    statusEl.style.color = '#ff4444';
  }
}

// ── Start ─────────────────────────────────────────────────────────────────────
function startRec() {
  if (!stream) { statusEl.textContent='Nema mikrofona!'; return; }
  chunks = [];
  document.getElementById('downloadWrap').style.dis = 'none';

  // Prefer wav-compatible format
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')
    ? 'audio/ogg;codecs=opus'
    : 'audio/webm';

  mediaRecorder = new MediaRecorder(stream, { mimeType });
  mediaRecorder.ondataavailable = e => { if(e.data.size>0) chunks.push(e.data); };
  mediaRecorder.onstop = buildDownload;
  mediaRecorder.start(100);

  isRec = true;
  seconds = 0;
  timerEl.textContent   = '00:00';
  timerEl.style.color   = '#ff4444';
  recDot.textContent    = '● REC';
  recDot.style.color    = '#ff4444';
  statusEl.textContent  = 'Snimanje u tijeku...';
  statusEl.style.color  = '#ff4444';

  document.getElementById('btnStart').disabled = true;
  document.getElementById('btnStart').style.background = '#552200';
  document.getElementById('btnStart').style.color = '#888';
  document.getElementById('btnStop').disabled  = false;
  document.getElementById('btnStop').style.background = '#ff4444';
  document.getElementById('btnStop').style.color = '#fff';
  document.getElementById('btnStop').style.cursor = 'pointer';

  timerInt = setInterval(()=>{ seconds++; timerEl.textContent=pad(Math.floor(seconds/60))+':'+pad(seconds%60); },1000);
}

// ── Stop ──────────────────────────────────────────────────────────────────────
function stopRec() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  clearInterval(timerInt);
  isRec = false;
  recDot.textContent  = '■ DONE';
  recDot.style.color  = '#44cc88';
  timerEl.style.color = '#44cc88';
  statusEl.textContent= 'Snimanje završeno';
  statusEl.style.color= '#44cc88';

  document.getElementById('btnStart').disabled = false;
  document.getElementById('btnStart').style.background = '#ff6600';
  document.getElementById('btnStart').style.color = '#000';
  document.getElementById('btnStop').disabled  = true;
  document.getElementById('btnStop').style.background = '#333';
  document.getElementById('btnStop').style.color = '#666';
  document.getElementById('btnStop').style.cursor = 'not-allowed';
}

// ── Build download ────────────────────────────────────────────────────────────
function buildDownload() {
  recCount++;
  const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
  const url  = URL.createObjectURL(blob);
  const ext  = mediaRecorder.mimeType.includes('ogg') ? 'ogg' : 'webm';
  const name = 'snimka_' + pad(recCount) + '.' + ext;

  document.getElementById('audioer').src = url;
  const dlBtn = document.getElementById('downloadBtn');
  dlBtn.href     = url;
  dlBtn.download = name;
  dlBtn.textContent = '⬇ SPREMI NA DISK (' + name + ')';
  document.getElementById('downloadWrap').style.dis = 'block';
}

drawLoop();
window.addEventListener('load', initMic);
</script>
"""

# ── Timecode helper ───────────────────────────────────────────────────────────
def ms_to_tc(ms):
    total_s = ms // 1000
    h  = total_s // 3600
    m  = (total_s % 3600) // 60
    s  = total_s % 60
    cs = (ms % 1000) // 10
    return f"{h:02d}:{m:02d}:{s:02d}.{cs:02d}"

def upload_with_progress(audio_bytes):
    CHUNK = 32768
    total = len(audio_bytes)
    uploaded = 0
    start_time = time.time()
    pbar  = st.progress(0.0, text="📤 Uploading...")
    speed = st.empty()

    def data_gen():
        nonlocal uploaded
        for i in range(0, total, CHUNK):
            chunk     = audio_bytes[i:i+CHUNK]
            uploaded += len(chunk)
            elapsed   = max(time.time()-start_time, 0.001)
            kb_s      = (uploaded/elapsed)/1024
            pbar.progress(uploaded/total,
                text=f"📤  {uploaded//1024} KB / {total//1024} KB   ⚡ {kb_s:.0f} KB/s")
            yield chunk

    resp = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers={**HEADERS, "content-type": "application/octet-stream"},
        data=data_gen()
    )
    pbar.progress(1.0, text="✓ Upload završen!")
    speed.empty()
    time.sleep(0.4)
    pbar.empty()
    resp.raise_for_status()
    return resp.json()["upload_url"]

def transcribe(audio_bytes, filename="audio"):
    upload_url = upload_with_progress(audio_bytes)
    st.info("✓ Uploadano. Pokrećem transkripciju...")

    tr = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**HEADERS, "content-type": "application/json"},
        json={
            "audio_url":     upload_url,
            "language_code": lang_code,
            "speech_models": ["universal-2"],
            "punctuate":     True,
            "format_text":   True,
        }
    )
    tr.raise_for_status()
    tid = tr.json()["id"]

    poll_url = f"https://api.assemblyai.com/v2/transcript/{tid}"
    ph = st.empty()
    attempts = 0
    while True:
        time.sleep(3)
        poll = requests.get(poll_url, headers=HEADERS).json()
        attempts += 1
        ph.info(f"⏳ Transkripcija u tijeku... ({attempts*3}s)")
        if poll.get("status") == "completed":
            ph.empty(); break
        elif poll.get("status") == "error":
            st.error(f"Greška: {poll.get('error')}"); st.stop()
        elif attempts > 120:
            st.error("Timeout."); st.stop()

    if include_timecode and poll.get("words"):
        words = poll["words"]
        lines, cur, cur_start = [], [], words[0]["start"]
        for i, w in enumerate(words):
            cur.append(w["text"])
            if len(cur) >= 10 or i == len(words)-1:
                lines.append(f"[{ms_to_tc(cur_start)}]  {' '.join(cur)}")
                cur = []
                if i < len(words)-1:
                    cur_start = words[i+1]["start"]
        return "\n\n".join(lines)
    return poll.get("text", "")

# ── UI ────────────────────────────────────────────────────────────────────────
output_text = None
download_filename = None

if input_mode == "🎤 Snimi + spremi + upload":
    st.markdown("**Korak 1 — Snimi i spremi na disk:**")
    st.components.v1.html(RECORDER_HTML, height=360)
    st.markdown("**Korak 2 — Uploadaj snimljenu datoteku:**")

# File uploader — prikazan uvijek, ali u mic modu s uputom
uploaded_file = st.file_uploader(
    "Učitaj audio datoteku" if input_mode == "📁 Upload datoteke" else "Uploadaj snimku s diska",
    type=["mp3", "mp4", "wav", "m4a", "aac", "ogg", "flac", "webm", "mov", "mxf"],
)

if uploaded_file:
    st.markdown(
        f'<div class="status-box">📂 <strong>{uploaded_file.name}</strong> — {lang_label} — '
        f'{uploaded_file.size // 1024} KB</div>',
        unsafe_allow_html=True
    )
    if st.button("▶  POKRETANJE TRANSKRIPCIJE"):
        try:
            output_text = transcribe(uploaded_file.read(), uploaded_file.name)
            base = os.path.splitext(uploaded_file.name)[0]
            tc_s = "_timecode" if include_timecode else ""
            download_filename = f"{base}_{lang_code}{tc_s}.txt"
        except requests.exceptions.HTTPError as e:
            st.error(f"HTTP greška: {e.response.status_code} — {e.response.text}")
        except Exception as e:
            st.error(f"Greška: {str(e)}")

if output_text:
    st.success("✅ Transkripcija završena!")
    st.text_area("REZULTAT", output_text, height=400)
    st.download_button(
        label="⬇  PREUZMI TXT DATOTEKU",
        data=output_text.encode("utf-8"),
        file_name=download_filename,
        mime="text/plain"
    )
