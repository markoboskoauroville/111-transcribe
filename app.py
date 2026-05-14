import streamlit as st
import requests
import time
import os
import base64

# ── API key ───────────────────────────────────────────────────────────────────
API_KEY = st.secrets["ASSEMBLYAI_API_KEY"]
HEADERS = {"authorization": API_KEY}

# ── Dizajn ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Marko Transcribe", page_icon="🎙️", layout="centered")
st.markdown('<div style="position:fixed;top:8px;left:12px;color:#666;font-size:12px;z-index:9999;font-family:monospace;">v1.0</div>', unsafe_allow_html=True)

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
LANGUAGE_MAP = {
    "Hrvatski": "hr",
    "English":  "en",
    "Italiano": "it",
    "Deutsch":  "de",
    "Français": "fr",
}
lang_label = st.radio("JEZIK / LANGUAGE", list(LANGUAGE_MAP.keys()), horizontal=True)
lang_code  = LANGUAGE_MAP[lang_label]

# ── Timecode ──────────────────────────────────────────────────────────────────
timecode_option  = st.radio("TIMECODE U TEKSTU", ["Bez timecoda", "S timecodeom"], horizontal=True)
include_timecode = timecode_option == "S timecodeom"

st.markdown("---")

# ── Input mode ────────────────────────────────────────────────────────────────
input_mode = st.radio("IZVOR ZVUKA", ["📁 Upload datoteke", "🎤 Snimi kroz browser"], horizontal=True)

# ── Browser mikrofon snimanje ─────────────────────────────────────────────────
RECORDER_HTML = """
<div style="background:#222;border:1px solid #444;border-radius:8px;padding:20px;margin:12px 0;">
  <div id="status" style="color:#888;font-size:13px;margin-bottom:12px;font-family:monospace;">
    ● Pritisni START za snimanje
  </div>
  <button onclick="startRec()" id="btnStart"
    style="background:#ff6600;color:#000;border:none;border-radius:4px;padding:10px 24px;
           font-weight:700;letter-spacing:1px;cursor:pointer;margin-right:10px;">
    ▶ START
  </button>
  <button onclick="stopRec()" id="btnStop" disabled
    style="background:#333;color:#888;border:1px solid #555;border-radius:4px;padding:10px 24px;
           font-weight:700;letter-spacing:1px;cursor:not-allowed;">
    ■ STOP
  </button>

  <div id="audioWrap" style="margin-top:16px;display:none;">
    <audio id="audioPlayer" controls style="width:100%;margin-bottom:10px;"></audio>
    <button onclick="sendAudio()"
      style="background:#ff6600;color:#000;border:none;border-radius:4px;padding:10px 24px;
             font-weight:700;letter-spacing:1px;cursor:pointer;width:100%;">
      ⬆ POŠALJI NA TRANSKRIPCIJU
    </button>
  </div>
  <div id="result" style="margin-top:12px;color:#aaa;font-size:13px;font-family:monospace;"></div>
</div>

<script>
let mediaRecorder, chunks = [], audioBlob;

async function startRec() {
  chunks = [];
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = e => chunks.push(e.data);
  mediaRecorder.onstop = () => {
    audioBlob = new Blob(chunks, { type: 'audio/webm' });
    const url = URL.createObjectURL(audioBlob);
    document.getElementById('audioPlayer').src = url;
    document.getElementById('audioWrap').style.display = 'block';
    document.getElementById('status').innerHTML = '✓ Snimanje završeno — preslušaj ili pošalji';
    document.getElementById('status').style.color = '#ff6600';
  };
  mediaRecorder.start();
  document.getElementById('status').innerHTML = '🔴 Snimanje u tijeku...';
  document.getElementById('status').style.color = '#ff4444';
  document.getElementById('btnStart').disabled = true;
  document.getElementById('btnStart').style.background = '#555';
  document.getElementById('btnStop').disabled = false;
  document.getElementById('btnStop').style.background = '#ff4444';
  document.getElementById('btnStop').style.color = '#fff';
  document.getElementById('btnStop').style.cursor = 'pointer';
}

function stopRec() {
  mediaRecorder.stop();
  mediaRecorder.stream.getTracks().forEach(t => t.stop());
  document.getElementById('btnStart').disabled = false;
  document.getElementById('btnStart').style.background = '#ff6600';
  document.getElementById('btnStop').disabled = true;
  document.getElementById('btnStop').style.background = '#333';
  document.getElementById('btnStop').style.color = '#888';
}

async function sendAudio() {
  document.getElementById('result').textContent = '📤 Šaljem na Streamlit...';
  const reader = new FileReader();
  reader.onload = function(e) {
    const b64 = e.target.result.split(',')[1];
    window.parent.postMessage({ type: 'streamlit:setComponentValue', value: b64 }, '*');
  };
  reader.readAsDataURL(audioBlob);
}
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

# ── Transkripcija (zajednička funkcija) ───────────────────────────────────────
def transcribe(audio_bytes, filename="recording.webm"):
    with st.spinner("📤 Uploading audio..."):
        upload_response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={**HEADERS, "content-type": "application/octet-stream"},
            data=audio_bytes
        )
        upload_response.raise_for_status()
        upload_url = upload_response.json()["upload_url"]

    st.info("✓ Audio uploadano. Pokrećem transkripciju...")

    transcript_response = requests.post(
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
    transcript_response.raise_for_status()
    transcript_id = transcript_response.json()["id"]

    polling_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    status_placeholder = st.empty()
    attempts = 0

    while True:
        time.sleep(3)
        poll = requests.get(polling_url, headers=HEADERS).json()
        attempts += 1
        status_placeholder.info(f"⏳ Transkripcija u tijeku... ({attempts * 3}s)")

        if poll.get("status") == "completed":
            status_placeholder.empty()
            break
        elif poll.get("status") == "error":
            st.error(f"Greška: {poll.get('error')}")
            st.stop()
        elif attempts > 120:
            st.error("Timeout.")
            st.stop()

    if include_timecode and poll.get("words"):
        words = poll["words"]
        lines, current_words = [], []
        current_start = words[0]["start"]
        for i, word in enumerate(words):
            current_words.append(word["text"])
            if len(current_words) >= 10 or i == len(words) - 1:
                lines.append(f"[{ms_to_tc(current_start)}]  {' '.join(current_words)}")
                current_words = []
                if i < len(words) - 1:
                    current_start = words[i + 1]["start"]
        return "\n\n".join(lines), filename
    else:
        return poll.get("text", ""), filename

# ── UI logika ─────────────────────────────────────────────────────────────────
output_text = None
download_filename = None

if input_mode == "📁 Upload datoteke":
    uploaded_file = st.file_uploader(
        "Učitaj audio datoteku",
        type=["mp3", "mp4", "wav", "m4a", "aac", "ogg", "flac", "mov", "mxf"],
    )
    if uploaded_file is not None:
        st.markdown(
            f'<div class="status-box">📂 <strong>{uploaded_file.name}</strong> — {lang_label}</div>',
            unsafe_allow_html=True
        )
        if st.button("▶  POKRETANJE TRANSKRIPCIJE"):
            try:
                output_text, fn = transcribe(uploaded_file.read(), uploaded_file.name)
                base = os.path.splitext(fn)[0]
                tc_suffix = "_timecode" if include_timecode else ""
                download_filename = f"{base}_{lang_code}{tc_suffix}.txt"
            except requests.exceptions.HTTPError as e:
                st.error(f"HTTP greška: {e.response.status_code} — {e.response.text}")
            except Exception as e:
                st.error(f"Greška: {str(e)}")

else:
    st.markdown("**Snimi audio kroz browser:**")
    st.components.v1.html(RECORDER_HTML, height=260)
    st.markdown("---")
    b64_audio = st.text_input(
        "Nakon snimanja klikni POŠALJI gore, pa zalijepi primljeni kod ovdje:",
        placeholder="base64 audio kod...",
        label_visibility="visible"
    )
    if b64_audio and st.button("▶  TRANSKRIBIRAJ SNIMKU"):
        try:
            audio_bytes = base64.b64decode(b64_audio)
            output_text, _ = transcribe(audio_bytes, "browser_recording.webm")
            tc_suffix = "_timecode" if include_timecode else ""
            download_filename = f"browser_recording_{lang_code}{tc_suffix}.txt"
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
