import streamlit as st
import requests
import time
import os
import json
import asyncio
import base64
import threading
import re
import ipaddress
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

# ── Secrets ───────────────────────────────────────────────────────────────────
API_KEY        = st.secrets["ASSEMBLYAI_API_KEY"]
HEADERS        = {"authorization": API_KEY}
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")
SETTINGS_FILE  = Path("/tmp/Marko_settings.json")

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except:
            pass
    return {
        "sheet_url": st.secrets.get("GOOGLE_SHEET_URL", ""),
        "app_title": "Marko TRANSCRIBE",
    }

def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False))

cfg = load_settings()

# ── Google Sheets ─────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource(ttl=300)
def get_sheet(sheet_url):
    try:
        creds  = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
        client = gspread.authorize(creds)
        ws     = client.open_by_url(sheet_url).sheet1
        if not ws.row_values(1) or ws.row_values(1)[0] != "date":
            ws.insert_row([
                "date","time","filename","lang","duration_sec",
                "first_words","last_words","ip","city","country",
                "org","isp","owner","tag"], 1)
        return ws
    except:
        return None

def sheet_append(row_dict):
    ws = get_sheet(cfg["sheet_url"])
    if not ws:
        return
    try:
        ws.append_row([
            row_dict.get("date",""), row_dict.get("time",""),
            row_dict.get("filename",""), row_dict.get("lang",""),
            row_dict.get("duration_sec",0), row_dict.get("first_words",""),
            row_dict.get("last_words",""), row_dict.get("ip",""),
            row_dict.get("city",""), row_dict.get("country",""),
            row_dict.get("org",""), row_dict.get("isp",""),
            row_dict.get("owner",""), row_dict.get("tag",""),
        ], value_input_option="USER_ENTERED")
    except:
        pass

def sheet_load():
    ws = get_sheet(cfg["sheet_url"])
    if not ws:
        return []
    try:
        return list(reversed(ws.get_all_records()))
    except:
        return []

def sheet_clear_log():
    ws = get_sheet(cfg["sheet_url"])
    if not ws:
        return
    try:
        ws.clear()
        ws.insert_row([
            "date","time","filename","lang","duration_sec",
            "first_words","last_words","ip","city","country",
            "org","isp","owner","tag"], 1)
    except:
        pass

# ── IP helpers ────────────────────────────────────────────────────────────────
def is_private(ip_str):
    try:
        return ipaddress.ip_address(ip_str).is_private
    except:
        return True

def get_client_ip():
    try:
        fwd = st.context.headers.get("X-Forwarded-For", "")
        if fwd:
            for candidate in [ip.strip() for ip in fwd.split(",")]:
                if candidate and not is_private(candidate):
                    return candidate
        real = st.context.headers.get("X-Real-IP", "")
        if real and not is_private(real):
            return real
        r = requests.get("https://api.ipify.org?format=json", timeout=4)
        return r.json().get("ip", "unknown")
    except:
        return "unknown"

def get_ip_info(ip):
    if ip in ("unknown", "127.0.0.1", ""):
        return {"city":"Local","country":"","org":"localhost","isp":""}
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,city,org,isp",
            timeout=5)
        d = r.json()
        if d.get("status") == "success":
            return {"city":d.get("city",""),"country":d.get("country",""),
                    "org":d.get("org",""),"isp":d.get("isp","")}
    except:
        pass
    return {"city":"","country":"","org":"","isp":""}

def detect_owner(org, isp):
    combined = (org+" "+isp).lower()
    if any(k in combined for k in ["nova tv","nova broadcasting","styria","central european media"]):
        return "NOVA TV","nova"
    if any(k in combined for k in ["t-hrvatski telekom","htnet","t-com","ht-","croatian telecom","hrvatski telekom"]):
        return "HT / T-Com (HR)","other"
    if combined.strip() in ("","localhost"):
        return "NEPOZNATO","unknown"
    return (org[:30] if org else isp[:30]),"other"

def format_duration(sec):
    try:
        sec = int(sec)
    except:
        return "0:00"
    return f"{sec//60}:{sec%60:02d}"

# ── Stereo → Mono via ffmpeg ──────────────────────────────────────────────────
def ensure_mono(audio_bytes, filename):
    ext = os.path.splitext(filename)[-1].lower() or ".mp3"
    if ext == ".mxf":
        ext = ".mp4"
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(audio_bytes)
            tmp_in = f.name

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=channels",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_in],
            capture_output=True, text=True)
        channels = probe.stdout.strip()

        if channels == "1":
            return audio_bytes, False

        tmp_out = tmp_in + "_mono.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in,
             "-ac", "1", "-af", "volume=-6dB", tmp_out],
            capture_output=True, check=True)

        with open(tmp_out, "rb") as f:
            result = f.read()
        return result, True

    except Exception as e:
        st.warning(f"Mono konverzija nije uspjela ({e}) — šaljem original.")
        return audio_bytes, False
    finally:
        for p in [tmp_in, tmp_out]:
            if p:
                try:
                    os.unlink(p)
                except:
                    pass

# ════════════════════════════════════════════════════════════════════════════
# EDGE TTS
# ════════════════════════════════════════════════════════════════════════════
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

def build_tts_player(text: str, audio_b64: str, word_boundaries: list) -> str:
    tokens   = re.split(r"(\s+)", text)
    word_idx = 0
    spans    = ""
    for tok in tokens:
        if not tok:
            continue
        if re.fullmatch(r"\s+", tok):
            spans += tok.replace("\n", "<br>")
        else:
            spans    += f'<span class="w" data-wi="{word_idx}">{tok}</span>'
            word_idx += 1
    wb_json     = json.dumps(word_boundaries)
    total_words = word_idx
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<style>
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:#111;color:#e0e0e0;font-family:'Inter',sans-serif;padding:0;}}
  #reader{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;
    padding:20px 24px;font-size:17px;line-height:2.1;max-height:280px;
    overflow-y:auto;margin-bottom:14px;color:#ccc;scroll-behavior:smooth;}}
  #reader::-webkit-scrollbar{{width:3px;}}
  #reader::-webkit-scrollbar-thumb{{background:#333;border-radius:3px;}}
  .w{{display:inline;border-radius:3px;padding:1px 2px;margin:0 -1px;
      transition:background .08s,color .08s;cursor:default;}}
  .w.read{{color:#555;}}
  .w.active{{background:#ff6600;color:#000;font-weight:700;border-radius:4px;}}
  #wp-wrap{{height:4px;background:#1e1e1e;border-radius:2px;margin-bottom:12px;overflow:hidden;}}
  #wp-fill{{height:100%;background:linear-gradient(90deg,#ff6600,#ffaa00);width:0%;border-radius:2px;transition:width .1s;}}
  #controls{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}}
  .btn{{display:inline-flex;align-items:center;gap:5px;padding:8px 16px;border:none;
        border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;}}
  #play-btn{{background:#ff6600;color:#000;min-width:100px;justify-content:center;}}
  #play-btn:hover{{background:#ff8833;}}
  #stop-btn{{background:#222;color:#888;border:1px solid #333;}}
  #stop-btn:hover{{background:#2a2a2a;color:#bbb;}}
  #seek{{flex:1;min-width:100px;-webkit-appearance:none;appearance:none;height:4px;
         background:#222;border-radius:4px;outline:none;cursor:pointer;}}
  #seek::-webkit-slider-thumb{{-webkit-appearance:none;width:13px;height:13px;
    border-radius:50%;background:#ff6600;cursor:pointer;}}
  #time-lbl{{font-family:monospace;font-size:11px;color:#555;min-width:84px;text-align:right;}}
  .ctrl-group{{display:flex;align-items:center;gap:5px;}}
  .ctrl-lbl{{font-size:11px;color:#555;font-family:monospace;}}
  select{{background:#1a1a1a;color:#aaa;border:1px solid #333;border-radius:6px;
          padding:5px 8px;font-family:monospace;font-size:11px;cursor:pointer;outline:none;}}
  #status{{margin-top:8px;font-size:11px;font-family:monospace;color:#444;text-align:right;}}
</style></head><body>
<div id="wp-wrap"><div id="wp-fill"></div></div>
<div id="reader">{spans}</div>
<div id="controls">
  <button class="btn" id="play-btn" onclick="togglePlay()">▶ Play</button>
  <button class="btn" id="stop-btn" onclick="stopAudio()">■ Reset</button>
  <input type="range" id="seek" min="0" step="0.01" value="0">
  <span id="time-lbl">0:00 / 0:00</span>
  <div class="ctrl-group">
    <span class="ctrl-lbl">Speed</span>
    <select id="speed" onchange="changeSpeed()">
      <option value="0.75">0.75×</option>
      <option value="1" selected>1.00×</option>
      <option value="1.25">1.25×</option>
      <option value="1.5">1.50×</option>
      <option value="2">2.00×</option>
    </select>
  </div>
</div>
<div id="status">ready · {total_words} words</div>
<audio id="audio" preload="auto" src="data:audio/mp3;base64,{audio_b64}"></audio>
<script>
const boundaries={wb_json},totalWords={total_words};
const audio=document.getElementById('audio'),seekBar=document.getElementById('seek');
const timeLbl=document.getElementById('time-lbl'),playBtn=document.getElementById('play-btn');
const statusLbl=document.getElementById('status'),wpFill=document.getElementById('wp-fill');
let activeIdx=-1;
const fmt=s=>{{const m=Math.floor(s/60),sec=Math.floor(s%60);return m+':'+(sec<10?'0':'')+sec;}};
audio.addEventListener('loadedmetadata',()=>{{seekBar.max=audio.duration;timeLbl.textContent='0:00 / '+fmt(audio.duration);}});
audio.addEventListener('timeupdate',()=>{{
  const t=audio.currentTime;seekBar.value=t;
  timeLbl.textContent=fmt(t)+' / '+fmt(audio.duration||0);
  let lo=0,hi=boundaries.length-1,found=-1;
  while(lo<=hi){{const mid=(lo+hi)>>1,b=boundaries[mid];
    if(t>=b.offset&&t<b.offset+b.duration+0.06){{found=mid;break;}}
    else if(b.offset>t){{hi=mid-1;}}else{{lo=mid+1;}}}}
  if(found===-1){{for(let i=boundaries.length-1;i>=0;i--){{if(boundaries[i].offset<=t){{found=i;break;}}}}}}
  if(found===activeIdx)return;activeIdx=found;
  document.querySelectorAll('.w').forEach((el,i)=>{{
    el.classList.remove('active','read');
    if(i===found)el.classList.add('active');
    else if(i<found)el.classList.add('read');
  }});
  if(found>=0){{
    const el=document.querySelector(`.w[data-wi="${{found}}"]`);
    if(el)el.scrollIntoView({{block:'nearest',behavior:'smooth'}});
    wpFill.style.width=((found+1)/totalWords*100).toFixed(1)+'%';
    statusLbl.textContent=`word ${{found+1}} of ${{totalWords}} · "${{boundaries[found].text}}"`;
  }}
}});
audio.addEventListener('ended',()=>{{playBtn.innerHTML='▶ Play';wpFill.style.width='100%';statusLbl.textContent=`done · ${{totalWords}} words`;}});
seekBar.addEventListener('input',()=>{{audio.currentTime=seekBar.value;}});
function togglePlay(){{if(audio.paused){{audio.play();playBtn.innerHTML='⏸ Pause';}}else{{audio.pause();playBtn.innerHTML='▶ Play';}}}}
function stopAudio(){{audio.pause();audio.currentTime=0;seekBar.value=0;wpFill.style.width='0%';
  timeLbl.textContent='0:00 / '+fmt(audio.duration||0);playBtn.innerHTML='▶ Play';activeIdx=-1;
  document.querySelectorAll('.w').forEach(el=>el.classList.remove('active','read'));
  statusLbl.textContent=`ready · ${{totalWords}} words`;}}
function changeSpeed(){{audio.playbackRate=parseFloat(document.getElementById('speed').value);}}
</script></body></html>"""

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title=cfg["app_title"], page_icon="🎙️", layout="centered")
st.markdown(
    '<div style="position:fixed;top:8px;right:12px;color:#555;font-size:11px;'
    'z-index:9999;font-family:monospace;">v2.6</div>',
    unsafe_allow_html=True)

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  html,body,[class*="css"]{font-family:'Inter',sans-serif;background:#1a1a1a;color:#e0e0e0;}
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
    ("_cached_file_size",  0),
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
# USAGE STATS
# ════════════════════════════════════════════════════════════════════════════
log_entries   = sheet_load() if cfg["sheet_url"] else []
RATE_PER_SEC  = 0.15 / 3600
TOTAL_CREDITS = 50.00
used_dollars  = sum(int(e.get("duration_sec", 0)) for e in log_entries) * RATE_PER_SEC
remaining_usd = max(0.0, TOTAL_CREDITS - used_dollars)
remaining_hrs = remaining_usd / 0.15
remaining_min = remaining_hrs * 60
pct           = min(1.0, used_dollars / TOTAL_CREDITS)
bar_color     = "#44cc88" if pct < 0.7 else "#ffaa00" if pct < 0.9 else "#ff4444"
time_left_str = (f"{remaining_hrs:.1f} h  ({remaining_min:.0f} min)"
                 if remaining_hrs >= 1.0 else f"{remaining_min:.0f} min")

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

def upload_with_progress(audio_bytes):
    CHUNK=32768; total=len(audio_bytes); uploaded=0; start=time.time()
    pbar=st.progress(0.0, text="Uploading...")
    def gen():
        nonlocal uploaded
        for i in range(0,total,CHUNK):
            chunk=audio_bytes[i:i+CHUNK]; uploaded+=len(chunk)
            elapsed=max(time.time()-start,0.001)
            pbar.progress(uploaded/total,
                text=f"{uploaded//1024} KB / {total//1024} KB   {(uploaded/elapsed)/1024:.0f} KB/s")
            yield chunk
    resp=requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers={**HEADERS,"content-type":"application/octet-stream"},
        data=gen())
    pbar.progress(1.0, text="Upload done!")
    time.sleep(0.3); pbar.empty()
    resp.raise_for_status()
    return resp.json()["upload_url"]

def transcribe(audio_bytes, filename="audio", lang_choice="Auto detect", include_timecode=False):
    audio_bytes, was_converted = ensure_mono(audio_bytes, filename)
    if was_converted:
        st.info("Stereo to mono conversion done.")

    upload_url = upload_with_progress(audio_bytes)
    st.info("Uploaded. Starting transcription...")

    if lang_choice == "Hrvatski":
        lang_params = {"language_code": "hr"}
    elif lang_choice == "English":
        lang_params = {"language_code": "en"}
    else:
        lang_params = {"language_detection": True}

    tr = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**HEADERS, "content-type": "application/json"},
        json={
            "audio_url":     upload_url,
            **lang_params,
            "speech_models": ["universal-3-pro", "universal-2"],
            "punctuate":     True,
            "format_text":   True,
        })
    tr.raise_for_status()
    tid = tr.json()["id"]
    ph = st.empty(); attempts = 0; poll = {}

    while True:
        time.sleep(3)
        poll = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{tid}",
            headers=HEADERS).json()
        attempts += 1
        ph.info(f"Processing... ({attempts*3}s)")
        if poll.get("status") == "completed":
            ph.empty(); break
        elif poll.get("status") == "error":
            st.error(f"Error: {poll.get('error')}"); st.stop()
        elif attempts > 120:
            st.error("Timeout."); st.stop()

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
    words_plain  = (poll.get("text", "") or "").split()
    client_ip    = get_client_ip()
    ip_info      = get_ip_info(client_ip)
    owner, tag   = detect_owner(ip_info.get("org",""), ip_info.get("isp",""))
    now_t        = datetime.now()

    entry = {
        "date":         now_t.strftime("%Y-%m-%d"),
        "time":         now_t.strftime("%H:%M:%S"),
        "filename":     filename,
        "lang":         detected_label,
        "duration_sec": duration_sec,
        "first_words":  " ".join(words_plain[:3]),
        "last_words":   " ".join(words_plain[-3:]),
        "ip":           client_ip,
        "city":         ip_info.get("city",""),
        "country":      ip_info.get("country",""),
        "org":          ip_info.get("org",""),
        "isp":          ip_info.get("isp",""),
        "owner":        owner,
        "tag":          tag,
    }
    if cfg["sheet_url"]:
        sheet_append(entry)

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

# ════════════════════════════════════════════════════════════════════════════
# 3-TAB LAYOUT
# ════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["Transcript", "Translation", "TTS", "CRE"])


# ─────────────────────────────────────────────────────
# TAB 1 — TRANSCRIPT
# ─────────────────────────────────────────────────────
with tab1:
    has_transcript = bool(st.session_state.transcript_text)

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
                for k in ["transcript_text","detected_lang","trl_result","tts_input",
                          "subtitle_segments","trl_segments","audio_duration_ms",
                          "_cached_file_name","_cached_file_bytes"]:
                    st.session_state[k] = (
                        [] if k in ["subtitle_segments","trl_segments"]
                        else b"" if k == "_cached_file_bytes"
                        else "")
                st.session_state.download_filename = "transkript.txt"
                st.rerun()

        # ROW 2: SRT | Avid (only if subtitles available)
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

        if det_code:
            st.markdown(
                f'<div class="detected-lang">Detected: <strong>{det_label}</strong>'
                f' &nbsp;·&nbsp; <code>{det_code}</code></div>',
                unsafe_allow_html=True)

        st.text_area("", st.session_state.transcript_text, height=360,
                     label_visibility="collapsed", key="result_area")

    else:
        # ── UPLOAD / TRANSCRIBE VIEW ─────────────────
        uploaded_file = st.file_uploader(
            "",
            type=["mp3","mp4","m4a","wav","aac","ogg","flac","webm",
                  "mov","mxf","wma","opus","3gp","amr","mp2","mpga","mpeg"],
            label_visibility="collapsed")

        # Cache file bytes immediately on detection — fixes Android double-tap issue.
        # Streamlit reruns on file select; by the time Transcribe is tapped, the
        # widget may have reset. Session state preserves the bytes across reruns.
        if uploaded_file is not None:
            if st.session_state["_cached_file_name"] != uploaded_file.name:
                st.session_state["_cached_file_bytes"] = uploaded_file.read()
                st.session_state["_cached_file_name"]  = uploaded_file.name
                st.session_state["_cached_file_size"]  = uploaded_file.size

        have_file = bool(st.session_state["_cached_file_name"])

        if have_file:
            if st.button("Transcribe", use_container_width=True, key="do_transcribe"):
                _lc    = st.session_state.get("_lang_choice", "Auto detect")
                _tc    = st.session_state.get("_include_timecode", False)
                _bytes = st.session_state["_cached_file_bytes"]
                _name  = st.session_state["_cached_file_name"]
                try:
                    result_text, dur, det_code, det_label = transcribe(
                        _bytes, _name, _lc, _tc)
                    st.session_state.transcript_text   = result_text
                    st.session_state.tts_input         = result_text
                    st.session_state.detected_lang     = det_code
                    st.session_state.trl_result        = ""
                    st.session_state.trl_segments      = []
                    # Clear cache after successful transcription
                    st.session_state["_cached_file_bytes"] = b""
                    st.session_state["_cached_file_name"]  = ""
                    base = os.path.splitext(_name)[0]
                    tc_s = "_timecode" if _tc else ""
                    st.session_state.download_filename = f"{base}_{det_code}{tc_s}.txt"
                    st.rerun()
                except requests.exceptions.HTTPError as e:
                    st.error(f"HTTP error: {e.response.status_code} — {e.response.text}")
                except Exception as e:
                    st.error(f"Error: {str(e)}")

        # Primary language
        lang_choice = st.radio("Language", ["Hrvatski", "English", "Auto detect"],
                               horizontal=True)
        st.session_state["_lang_choice"] = lang_choice

        tc_opt = st.radio("Timecode", ["Off", "On"], horizontal=True)
        st.session_state["_include_timecode"] = tc_opt == "On"

        input_mode = st.radio("Source", ["Upload", "Rec"], horizontal=True)
        if input_mode == "Rec":
            st.components.v1.html(RECORDER_HTML, height=360)

        if uploaded_file:
            st.markdown(
                f'<div class="status-box"><strong>{uploaded_file.name}</strong>'
                f' — {lang_choice} — {uploaded_file.size//1024} KB</div>',
                unsafe_allow_html=True)

        # ── EXPANDED LANGUAGE LIST ────────────────────
        with st.expander("All languages (advanced)", expanded=False):
            ext_lang = st.selectbox(
                "Select any language for transcription",
                ["— use primary selector above —"] + sorted(EXTENDED_LANGUAGE_MAP.keys()),
                key="ext_lang_sel")
            if ext_lang != "— use primary selector above —":
                st.session_state["_lang_choice"] = ext_lang
                st.info(f"Set to: {ext_lang} ({EXTENDED_LANGUAGE_MAP.get(ext_lang,'')})")


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

    cp1, cp2 = st.columns(2)
    with cp1:
        if st.button("Pull transcript", use_container_width=True, key="tts_pull_tra"):
            st.session_state["tts_text_area"] = st.session_state.transcript_text or ""
            st.rerun()
    with cp2:
        if st.button("Pull translation", use_container_width=True, key="tts_pull_trl"):
            st.session_state["tts_text_area"] = st.session_state.trl_result or ""
            st.rerun()

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

    # Standard TTS
    if st.button("Generate", use_container_width=True, key="tts_btn"):
        clean = tts_text.strip()
        if not clean:
            st.warning("No text.")
        elif len(clean) > 15000:
            st.warning("Text too long (max 15 000 chars).")
        else:
            with st.spinner(f"Generating — {selected_voice}..."):
                try:
                    audio_data, wbs = generate_tts(clean, selected_voice)
                    if audio_data:
                        st.components.v1.html(
                            build_tts_player(clean, base64.b64encode(audio_data).decode(), wbs),
                            height=480, scrolling=False)
                        safe = re.sub(r'[^a-z0-9]+','_', tts_lang.lower())
                        st.download_button("Download audio (MP3)", data=audio_data,
                            file_name=f"tts_{safe}_{gender.lower()}.mp3",
                            mime="audio/mpeg", key="tts_download")
                except Exception as exc:
                    st.error(f"TTS error: {exc}")

    # Timed TTS (dubbed audio)
    st.markdown("---")
    st.markdown('<div style="font-family:monospace;font-size:11px;color:#555;'
                'letter-spacing:1px;margin-bottom:8px;">TIMED TTS — dubbed audio at original timestamps</div>',
                unsafe_allow_html=True)

    timed_segs = st.session_state.trl_segments or st.session_state.subtitle_segments
    if not timed_segs:
        st.markdown('<div style="color:#333;font-family:monospace;font-size:12px;">'
                    'No subtitle segments yet — transcribe audio first, then optionally translate.</div>',
                    unsafe_allow_html=True)
    else:
        total_ms = st.session_state.get("audio_duration_ms", 0)
        st.markdown(
            f'<div style="font-family:monospace;font-size:11px;color:#555;">'
            f'{len(timed_segs)} segments · {total_ms//1000}s total</div>',
            unsafe_allow_html=True)
        source_label = "translated" if st.session_state.trl_segments else "original"
        btn_label = "Generate timed audio (" + source_label + " · " + selected_voice.split("-")[0] + ")"
        if st.button(btn_label, use_container_width=True, key="tts_timed_btn"):
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
    real_balance = cfg.get("real_balance", None)
    if real_balance is not None:
        display_usd = real_balance
        offset      = real_balance - remaining_usd
    else:
        display_usd = remaining_usd
        offset      = 0.0

    display_pct   = min(1.0, (TOTAL_CREDITS - display_usd) / TOTAL_CREDITS)
    display_hrs   = display_usd / 0.15
    display_min   = display_hrs * 60
    disp_color    = "#44cc88" if display_pct < 0.7 else "#ffaa00" if display_pct < 0.9 else "#ff4444"
    disp_time     = (f"{display_hrs:.1f} h  ({display_min:.0f} min)"
                     if display_hrs >= 1.0 else f"{display_min:.0f} min")

    st.markdown(f"""
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:16px;margin-bottom:14px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
    <span style="font-family:monospace;font-size:11px;color:#555;letter-spacing:2px;">
      ASSEMBLYAI · $0.15/h · best model
    </span>
    <span style="font-family:monospace;font-size:15px;color:{disp_color};font-weight:700;">
      ${display_usd:.2f} remaining
    </span>
  </div>
  <div style="background:#1a1a1a;border-radius:4px;height:10px;overflow:hidden;margin-bottom:8px;">
    <div style="width:{int(display_pct*100)}%;height:100%;background:{disp_color};border-radius:4px;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-family:monospace;font-size:10px;color:#555;">
    <span>sheet estimate: ${remaining_usd:.3f}</span>
    <span>time left: {disp_time}</span>
  </div>
  {"" if real_balance is None else f'<div style="font-family:monospace;font-size:10px;color:#4a9;margin-top:4px;">✓ calibrated · offset {offset:+.3f}</div>'}
</div>
""", unsafe_allow_html=True)

    st.markdown('<div style="font-family:monospace;font-size:11px;color:#555;margin-bottom:6px;">'
                'Calibrate — enter real balance from AssemblyAI dashboard:</div>',
                unsafe_allow_html=True)
    col_bal, col_set = st.columns([3, 1])
    with col_bal:
        bal_input = st.number_input("", min_value=0.0, max_value=500.0,
            value=float(real_balance) if real_balance is not None else remaining_usd,
            step=0.01, format="%.2f", label_visibility="collapsed", key="real_balance_input")
    with col_set:
        if st.button("Set", use_container_width=True, key="set_balance_btn"):
            cfg["real_balance"] = round(bal_input, 4)
            save_settings(cfg)
            st.success(f"Calibrated to ${bal_input:.2f}")
            st.rerun()
    if real_balance is not None:
        if st.button("Clear calibration", key="clear_cal"):
            cfg.pop("real_balance", None)
            save_settings(cfg)
            st.rerun()
