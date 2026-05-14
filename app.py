import streamlit as st
import assemblyai as aai
import tempfile
import os

# ── API key iz Streamlit Secrets ──────────────────────────────────────────────
aai.settings.api_key = st.secrets["ASSEMBLYAI_API_KEY"]

# ── Avid-style dizajn ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Lana Transcribe",
    page_icon="🎙️",
    layout="centered"
)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background-color: #1a1a1a;
    color: #e0e0e0;
  }

  .stApp {
    background-color: #1a1a1a;
  }

  h1 {
    color: #00aaff;
    font-weight: 700;
    letter-spacing: 1px;
    border-bottom: 2px solid #00aaff;
    padding-bottom: 8px;
    margin-bottom: 4px;
  }

  .subtitle {
    color: #888;
    font-size: 0.85rem;
    margin-bottom: 24px;
    letter-spacing: 2px;
    text-transform: uppercase;
  }

  .stRadio > label {
    color: #aaa;
    font-size: 0.8rem;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  .stRadio div[role="radiogroup"] label {
    color: #ccc;
  }

  .stButton > button {
    background-color: #00aaff;
    color: #000;
    font-weight: 700;
    border: none;
    border-radius: 4px;
    padding: 10px 28px;
    letter-spacing: 1px;
    text-transform: uppercase;
    transition: background 0.2s;
  }

  .stButton > button:hover {
    background-color: #0088cc;
    color: #fff;
  }

  .stTextArea textarea {
    background-color: #2a2a2a;
    color: #e0e0e0;
    border: 1px solid #444;
    font-family: 'Courier New', monospace;
    font-size: 0.9rem;
  }

  .stFileUploader {
    border: 2px dashed #444;
    border-radius: 6px;
    padding: 12px;
    background-color: #222;
  }

  .stDownloadButton > button {
    background-color: #222;
    color: #00aaff;
    border: 1px solid #00aaff;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 1px;
  }

  .stDownloadButton > button:hover {
    background-color: #00aaff;
    color: #000;
  }

  .status-box {
    background-color: #222;
    border-left: 3px solid #00aaff;
    padding: 10px 16px;
    border-radius: 4px;
    margin: 12px 0;
    font-size: 0.9rem;
    color: #aaa;
  }
</style>
""", unsafe_allow_html=True)

# ── Naslov ────────────────────────────────────────────────────────────────────
st.markdown("<h1>🎙️ LANA TRANSCRIBE</h1>", unsafe_allow_html=True)
st.markdown('<div class="subtitle">Nova TV — Automated Interview Transcription</div>',
            unsafe_allow_html=True)

# ── Jezik ─────────────────────────────────────────────────────────────────────
LANGUAGE_MAP = {
    "Hrvatski": "hr",
    "English":  "en",
    "Italiano": "it",
    "Deutsch":  "de",
    "Français": "fr",
}

lang_label = st.radio(
    "JEZIK / LANGUAGE",
    list(LANGUAGE_MAP.keys()),
    horizontal=True
)
lang_code = LANGUAGE_MAP[lang_label]

# ── Timecode opcija ───────────────────────────────────────────────────────────
timecode_option = st.radio(
    "TIMECODE U TEKSTU",
    ["Bez timecoda", "S timecodeom"],
    horizontal=True
)
include_timecode = timecode_option == "S timecodeom"

# ── Upload ────────────────────────────────────────────────────────────────────
st.markdown("---")
uploaded_file = st.file_uploader(
    "Učitaj audio datoteku",
    type=["mp3", "mp4", "wav", "m4a", "aac", "ogg", "flac", "mov", "mxf"],
    help="Podržani formati: MP3, MP4, WAV, M4A, AAC, OGG, FLAC, MOV, MXF"
)

# ── Pomoćna funkcija: ms → SRT timecode format ────────────────────────────────
def ms_to_tc(ms):
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    centiseconds = (ms % 1000) // 10
    return f"{h:02d}:{m:02d}:{s:02d}.{centiseconds:02d}"

# ── Transkripcija ─────────────────────────────────────────────────────────────
if uploaded_file is not None:
    st.markdown(
        f'<div class="status-box">📂 Datoteka učitana: <strong>{uploaded_file.name}</strong>'
        f' — Jezik: <strong>{lang_label}</strong></div>',
        unsafe_allow_html=True
    )

    if st.button("▶  POKRETANJE TRANSKRIPCIJE"):
        with st.spinner("Spajanje s AssemblyAI... molim pričekaj."):

            # Spremi upload u privremenu datoteku
            suffix = os.path.splitext(uploaded_file.name)[-1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name

            try:
                config = aai.TranscriptionConfig(
                    language_code=lang_code,
                    punctuate=True,
                    format_text=True,
                )

                transcriber = aai.Transcriber()
                transcript = transcriber.transcribe(tmp_path, config=config)

                if transcript.status == aai.TranscriptStatus.error:
                    st.error(f"Greška u transkripciji: {transcript.error}")
                else:
                    # Gradi output tekst
                    if include_timecode and transcript.words:
                        lines = []
                        current_line = []
                        current_start = transcript.words[0].start

                        for word in transcript.words:
                            current_line.append(word.text)
                            # Novi redak svakih ~10 riječi
                            if len(current_line) >= 10:
                                tc = ms_to_tc(current_start)
                                lines.append(f"[{tc}]  {' '.join(current_line)}")
                                current_line = []
                                if word != transcript.words[-1]:
                                    next_idx = transcript.words.index(word) + 1
                                    current_start = transcript.words[next_idx].start

                        if current_line:
                            tc = ms_to_tc(current_start)
                            lines.append(f"[{tc}]  {' '.join(current_line)}")

                        output_text = "\n\n".join(lines)
                    else:
                        output_text = transcript.text

                    st.success("✅ Transkripcija završena!")

                    st.text_area(
                        "REZULTAT",
                        output_text,
                        height=400,
                        label_visibility="visible"
                    )

                    base_name = os.path.splitext(uploaded_file.name)[0]
                    tc_suffix = "_timecode" if include_timecode else ""
                    download_filename = f"{base_name}_{lang_code}{tc_suffix}.txt"

                    st.download_button(
                        label="⬇  PREUZMI TXT DATOTEKU",
                        data=output_text.encode("utf-8"),
                        file_name=download_filename,
                        mime="text/plain"
                    )

            finally:
                os.unlink(tmp_path)