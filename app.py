import streamlit as st
import requests
import time
import os
from streamlit_mic_recorder import mic_recorder

# ── API key ───────────────────────────────────────────────────────────────────
API_KEY = st.secrets["ASSEMBLYAI_API_KEY"]
HEADERS = {"authorization": API_KEY}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Marko Transcribe audio", page_icon="🎙️", layout="centered")
st.markdown('<div style="position:fixed;top:8px;left:12px;color:#666;font-size:12px;z-index:9999;font-family:monospace;">v1.3</div>', unsafe_allow_html=True)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
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
input_mode = st.radio("IZVOR ZVUKA", ["📁 Upload datoteke", "🎤 Snimi kroz browser"], horizontal=True)

# ── Waveform + Timer monitor (vizualni, koristi vlastiti mic pristup za display) ──
MONITOR_HTML = """
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:14px;margin-bottom:4px;">

  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <span style="color:#ff6600;font-size:11px;letter-spacing:3px;font-family:monospace;">AUDIO MONITOR</span>
    <span id="timer" style="color:#ff6600;font-size:22px;font-weight:700;font-family:monospace;letter-spacing:2px;">00:00</span>
    <span id="recDot" style="color:#333;font-size:11px;font-family:monospace;">● STANDBY</span>
  </div>

  <canvas id="waveCanvas" width="800" height="70"
    style="width:100%;height:70px;background:#0a0a0a;border-radius:4px;display:block;"></canvas>

  <div style="display:flex;justify-content:space-between;margin-top:8px;font-family:monospace;font-size:11px;">
    <span id="levelBar" style="color:#444;letter-spacing:1px;">▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯</span>
    <span id="statusMsg" style="color:#555;">initializing mic...</span>
  </div>

</div>

<script>
const canvas  = document.getElementById('waveCanvas');
const ctx     = canvas.getContext('2d');
const timerEl = document.getElementById('timer');
const recDot  = document.getElementById('recDot');
const levelEl = document.getElementById('levelBar');
const statusEl= document.getElementById('statusMsg');

let analyser, dataArray;
let timerInterval = null;
let seconds = 0;
let isRecording = false;
let lastLevel = 0;

function pad(n){ return String(n).padStart(2,'0'); }
function formatTime(s){ return pad(Math.floor(s/60)) + ':' + pad(s%60); }

// ── Waveform draw loop ────────────────────────────────────────────────────────
function drawLoop() {
  requestAnimationFrame(drawLoop);
  if (!analyser) { drawFlat(); return; }

  analyser.getByteTimeDomainData(dataArray);
  canvas.width = canvas.offsetWidth * (window.devicePixelRatio || 1);
  canvas.height = 70 * (window.devicePixelRatio || 1);
  canvas.style.height = '70px';

  ctx.fillStyle = '#0a0a0a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Centre line
  ctx.strokeStyle = '#1a1a1a';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, canvas.height/2);
  ctx.lineTo(canvas.width, canvas.height/2);
  ctx.stroke();

  // Waveform
  const color = isRecording ? '#ff6600' : '#444';
  ctx.lineWidth = isRecording ? 2 : 1;
  ctx.strokeStyle = color;
  ctx.shadowBlur  = isRecording ? 10 : 0;
  ctx.shadowColor = '#ff6600';
  ctx.beginPath();
  const sw = canvas.width / dataArray.length;
  for (let i = 0; i < dataArray.length; i++) {
    const v = dataArray[i] / 128.0;
    const y = (v * canvas.height) / 2;
    i === 0 ? ctx.moveTo(0, y) : ctx.lineTo(i * sw, y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Level meter
  let sum = 0;
  for (let i = 0; i < dataArray.length; i++) sum += Math.abs(dataArray[i] - 128);
  const level = sum / dataArray.length;
  lastLevel = level;
  const bars = Math.min(20, Math.round(level * 20 / 25));
  const filled = isRecording ? '▮' : '▪';
  levelEl.style.color = isRecording ? '#ff6600' : '#444';
  levelEl.textContent = filled.repeat(bars) + '▯'.repeat(20 - bars);
  statusEl.textContent = isRecording
    ? `level: ${Math.round(level)} dB`
    : (analyser ? 'mic ready — click START below' : 'no mic');
}

function drawFlat() {
  canvas.width  = canvas.offsetWidth;
  canvas.height = 70;
  ctx.fillStyle = '#0a0a0a';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.strokeStyle = '#222';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, 35); ctx.lineTo(canvas.width, 35);
  ctx.stroke();
}

// ── Timer control via postMessage from parent (not needed — we detect via DOM) ─
// We detect recording state by watching level changes after user clicks START
let watchInterval = null;
let levelHistory  = [];

function watchRecordingState() {
  // Heuristic: if level spikes after being flat, recording started
  // We listen for postMessage from streamlit_mic_recorder iframe
}

// Listen for any postMessage signals
window.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'rec_start') startTimer();
  if (e.data && e.data.type === 'rec_stop')  stopTimer();
});

function startTimer() {
  if (timerInterval) return;
  seconds = 0;
  isRecording = true;
  recDot.textContent  = '● REC';
  recDot.style.color  = '#ff4444';
  timerEl.style.color = '#ff4444';
  timerInterval = setInterval(() => {
    seconds++;
    timerEl.textContent = formatTime(seconds);
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
  timerInterval = null;
  isRecording   = false;
  recDot.textContent  = '■ STOPPED';
  recDot.style.color  = '#44cc88';
  timerEl.style.color = '#44cc88';
  statusEl.textContent = 'recording done — click POKRETANJE TRANSKRIPCIJE';
  statusEl.style.color = '#44cc88';
}

// ── Init microphone ───────────────────────────────────────────────────────────
async function initMic() {
  try {
    const stream  = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    const audioCtx= new (window.AudioContext || window.webkitAudioContext)();
    const source  = audioCtx.createMediaStreamSource(stream);
    analyser      = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    dataArray     = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);
    statusEl.textContent = 'mic connected — click START below';
    statusEl.style.color = '#ff6600';

    // Auto-detect recording start: watch for sustained level > threshold
    let highCount = 0;
    let lowCount  = 0;
    setInterval(() => {
      if (lastLevel > 3) { highCount++; lowCount = 0; }
      else               { lowCount++;  highCount = 0; }
      if (highCount === 3  && !isRecording) startTimer();
      if (lowCount  === 8  &&  isRecording) stopTimer();
    }, 200);

  } catch(e) {
    statusEl.textContent = 'mic denied: ' + e.message;
    statusEl.style.color = '#ff4444';
    drawFlat();
  }
}

drawLoop();
window.addEventListener('load', initMic);
</script>
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def ms_to_tc(ms):
    total_s = ms // 1000
    h  = total_s // 3600
    m  = (total_s % 3600) // 60
    s  = total_s % 60
    cs = (ms % 1000) // 10
    return f"{h:02d}:{m:02d}:{s:02d}.{cs:02d}"

def upload_with_progress(audio_bytes):
    """Chunked server-side upload s progress barom i speed indikatorom."""
    CHUNK = 32768  # 32 KB
    total = len(audio_bytes)
    uploaded = 0
    start_time = time.time()

    pbar  = st.progress(0.0, text="📤 Uploading...")
    speed = st.empty()

    def data_gen():
        nonlocal uploaded
        for i in range(0, total, CHUNK):
            chunk     = audio_bytes[i:i + CHUNK]
            uploaded += len(chunk)
            elapsed   = max(time.time() - start_time, 0.001)
            kb_s      = (uploaded / elapsed) / 1024
            pct       = uploaded / total
            pbar.progress(pct, text=f"📤  {uploaded // 1024} KB / {total // 1024} KB")
            speed.markdown(
                f"<span style='font-family:monospace;color:#ff6600;font-size:12px;'>"
                f"⚡ {kb_s:.0f} KB/s</span>",
                unsafe_allow_html=True
            )
            yield chunk

    resp = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers={**HEADERS, "content-type": "application/octet-stream"},
        data=data_gen()
    )
    pbar.progress(1.0, text="✓ Upload complete!")
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
        ph.info(f"⏳ Transkripcija u tijeku... ({attempts * 3}s)")
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
            if len(cur) >= 10 or i == len(words) - 1:
                lines.append(f"[{ms_to_tc(cur_start)}]  {' '.join(cur)}")
                cur = []
                if i < len(words) - 1:
                    cur_start = words[i + 1]["start"]
        return "\n\n".join(lines)
    return poll.get("text", "")

# ── UI ────────────────────────────────────────────────────────────────────────
output_text = None
download_filename = None

if input_mode == "📁 Upload datoteke":
    uploaded_file = st.file_uploader(
        "Učitaj audio datoteku",
        type=["mp3", "mp4", "wav", "m4a", "aac", "ogg", "flac", "mov", "mxf"],
    )
    if uploaded_file:
        st.markdown(
            f'<div class="status-box">📂 <strong>{uploaded_file.name}</strong> — {lang_label}</div>',
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

else:
    # Waveform + timer monitor
    st.components.v1.html(MONITOR_HTML, height=145)

    st.markdown(
        '<div class="status-box">🎤 Klikni <strong>START SNIMANJE</strong> ispod — '
        'monitor automatski detektira audio signal. '
        'Nakon snimanja klikni <strong>POKRETANJE TRANSKRIPCIJE</strong>.</div>',
        unsafe_allow_html=True
    )

    audio = mic_recorder(
        start_prompt="▶  START SNIMANJE",
        stop_prompt="■  STOP SNIMANJE",
        just_once=True,
        use_container_width=True,
        key="mic"
    )

    if audio and audio.get("bytes"):
        size_kb = len(audio["bytes"]) // 1024
        st.success(f"✓ Snimka primljena — {size_kb} KB — spreman za transkripciju")
        if st.button("▶  POKRETANJE TRANSKRIPCIJE"):
            try:
                output_text = transcribe(audio["bytes"], "mikrofon_snimka")
                tc_s = "_timecode" if include_timecode else ""
                download_filename = f"mikrofon_{lang_code}{tc_s}.txt"
            except requests.exceptions.HTTPError as e:
                st.error(f"HTTP greška: {e.response.status_code} — {e.response.text}")
            except Exception as e:
                st.error(f"Greška: {str(e)}")

# ── Output ────────────────────────────────────────────────────────────────────
if output_text:
    st.success("✅ Transkripcija završena!")
    st.text_area("REZULTAT", output_text, height=400)
    st.download_button(
        label="⬇  PREUZMI TXT DATOTEKU",
        data=output_text.encode("utf-8"),
        file_name=download_filename,
        mime="text/plain"
    )
