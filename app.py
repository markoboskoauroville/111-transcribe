import streamlit as st
import requests
import time
import os
import json
import asyncio
import base64
import threading
import re
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

# ── Secrets ───────────────────────────────────────────────────────────────────
API_KEY        = st.secrets["ASSEMBLYAI_API_KEY"]
HEADERS        = {"authorization": API_KEY}
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin123")

SETTINGS_FILE = Path("/tmp/marko_settings.json")

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except:
            pass
    return {
        "sheet_url":         st.secrets.get("GOOGLE_SHEET_URL", ""),
        "monthly_limit_min": int(st.secrets.get("MONTHLY_LIMIT_MIN", 180)),
        "app_title":         "MARKO TRANSCRIBE",
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
def get_client_ip():
    try:
        fwd = st.context.headers.get("X-Forwarded-For","")
        if fwd: return fwd.split(",")[0].strip()
        return st.context.headers.get("X-Real-IP","unknown")
    except:
        return "unknown"

def get_ip_info(ip):
    if ip in ("unknown","127.0.0.1",""):
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

# ════════════════════════════════════════════════════════════════════════════
# EDGE TTS
# ════════════════════════════════════════════════════════════════════════════

# Best voices per language + gender
VOICE_MAP = {
    "Hrvatski": {"🚺 Female": "hr-HR-GabrijelaNeural", "🚹 Male": "hr-HR-SreckoNeural"},
    "English":  {"🚺 Female": "en-US-AriaNeural",       "🚹 Male": "en-US-GuyNeural"},
    "Italiano": {"🚺 Female": "it-IT-ElsaNeural",        "🚹 Male": "it-IT-DiegoNeural"},
    "Deutsch":  {"🚺 Female": "de-DE-KatjaNeural",       "🚹 Male": "de-DE-ConradNeural"},
    "Français": {"🚺 Female": "fr-FR-DeniseNeural",      "🚹 Male": "fr-FR-HenriNeural"},
}

async def _tts_async(text: str, voice: str):
    import edge_tts
    communicate    = edge_tts.Communicate(text, voice)
    audio_chunks   = []
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
    tokens    = re.split(r"(\s+)", text)
    word_idx  = 0
    spans     = ""
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
  #reader{{
    background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;
    padding:20px 24px;font-size:17px;line-height:2.1;
    max-height:280px;overflow-y:auto;margin-bottom:14px;
    color:#ccc;scroll-behavior:smooth;
  }}
  #reader::-webkit-scrollbar{{width:3px;}}
  #reader::-webkit-scrollbar-thumb{{background:#333;border-radius:3px;}}
  .w{{display:inline;border-radius:3px;padding:1px 2px;margin:0 -1px;
      transition:background .08s,color .08s;cursor:default;}}
  .w.read{{color:#555;}}
  .w.active{{background:#ff6600;color:#000;font-weight:700;border-radius:4px;}}
  #wp-wrap{{height:4px;background:#1e1e1e;border-radius:2px;margin-bottom:12px;overflow:hidden;}}
  #wp-fill{{height:100%;background:linear-gradient(90deg,#ff6600,#ffaa00);width:0%;border-radius:2px;transition:width .1s;}}
  #controls{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}}
  .btn{{display:inline-flex;align-items:center;gap:5px;padding:8px 16px;
        border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;}}
  #play-btn{{background:#ff6600;color:#000;min-width:100px;justify-content:center;}}
  #play-btn:hover{{background:#ff8833;}}
  #stop-btn{{background:#222;color:#888;border:1px solid #333;}}
  #stop-btn:hover{{background:#2a2a2a;color:#bbb;}}
  #seek{{flex:1;min-width:100px;-webkit-appearance:none;appearance:none;
         height:4px;background:#222;border-radius:4px;outline:none;cursor:pointer;}}
  #seek::-webkit-slider-thumb{{-webkit-appearance:none;width:13px;height:13px;
    border-radius:50%;background:#ff6600;cursor:pointer;}}
  #time-lbl{{font-family:monospace;font-size:11px;color:#555;min-width:84px;text-align:right;}}
  .ctrl-group{{display:flex;align-items:center;gap:5px;}}
  .ctrl-lbl{{font-size:11px;color:#555;font-family:monospace;}}
  select{{background:#1a1a1a;color:#aaa;border:1px solid #333;border-radius:6px;
          padding:5px 8px;font-family:monospace;font-size:11px;cursor:pointer;outline:none;}}
  #status{{margin-top:8px;font-size:11px;font-family:monospace;color:#444;text-align:right;}}
</style>
</head><body>
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
    '<div style="position:fixed;top:8px;left:12px;color:#555;font-size:11px;'
    'z-index:9999;font-family:monospace;">v1.8</div>',
    unsafe_allow_html=True
)

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
    padding:10px 28px;letter-spacing:1px;text-transform:uppercase;}
  .stButton>button:hover{background:#cc5200;color:#fff;}
  .stTextArea textarea{background:#2a2a2a;color:#e0e0e0;border:1px solid #444;
    font-family:'Courier New',monospace;font-size:.9rem;}
  .stDownloadButton>button{background:#222;color:#ff6600;border:1px solid #ff6600;
    border-radius:4px;font-weight:600;}
  .stDownloadButton>button:hover{background:#ff6600;color:#000;}
  .status-box{background:#222;border-left:3px solid #ff6600;padding:10px 16px;
    border-radius:4px;margin:12px 0;font-size:.9rem;color:#aaa;}
  .stExpander{border:1px solid #2a2a2a !important;border-radius:6px !important;}
</style>
""", unsafe_allow_html=True)

st.markdown(f"<h1>🎙️ {cfg['app_title']}</h1>", unsafe_allow_html=True)
st.markdown('<div class="subtitle">Personal Transcription Tool</div>', unsafe_allow_html=True)

# ── Session state init ────────────────────────────────────────────────────────
if "transcript_text" not in st.session_state:
    st.session_state.transcript_text = ""
if "admin_ok" not in st.session_state:
    st.session_state.admin_ok = False
if "tts_open" not in st.session_state:
    st.session_state.tts_open = False
if "tts_input" not in st.session_state:
    st.session_state.tts_input = ""

# ════════════════════════════════════════════════════════════════════════════
# USAGE BAR
# ════════════════════════════════════════════════════════════════════════════
log_entries   = sheet_load() if cfg["sheet_url"] else []
monthly_sec   = cfg["monthly_limit_min"] * 60
now           = datetime.now()
month_prefix  = f"{now.year}-{now.month:02d}"
used_sec      = sum(int(e.get("duration_sec",0)) for e in log_entries
                    if str(e.get("date","")).startswith(month_prefix))
remaining_sec = max(0, monthly_sec - used_sec)
pct           = min(1.0, used_sec / monthly_sec) if monthly_sec else 0
bar_color     = "#44cc88" if pct < 0.7 else "#ffaa00" if pct < 0.9 else "#ff4444"

st.markdown(f"""
<div style="background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:14px;margin-bottom:16px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <span style="font-family:monospace;font-size:11px;color:#555;letter-spacing:2px;">
      ASSEMBLYAI — {now.strftime('%B %Y').upper()}
    </span>
    <span style="font-family:monospace;font-size:14px;color:{bar_color};font-weight:700;">
      ⏱ {remaining_sec//60}:{remaining_sec%60:02d} min preostalo
    </span>
  </div>
  <div style="background:#1a1a1a;border-radius:4px;height:10px;overflow:hidden;margin-bottom:6px;">
    <div style="width:{int(pct*100)}%;height:100%;background:{bar_color};border-radius:4px;"></div>
  </div>
  <div style="display:flex;justify-content:space-between;font-family:monospace;font-size:10px;color:#555;">
    <span>iskorišteno: {used_sec//60}:{used_sec%60:02d} min</span>
    <span>limit: {cfg['monthly_limit_min']} min</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
# TRANSCRIPTION SECTION
# ════════════════════════════════════════════════════════════════════════════
LANGUAGE_MAP = {"Hrvatski":"hr","English":"en","Italiano":"it","Deutsch":"de","Français":"fr"}
lang_label   = st.radio("JEZIK / LANGUAGE", list(LANGUAGE_MAP.keys()), horizontal=True)
lang_code    = LANGUAGE_MAP[lang_label]

timecode_option  = st.radio("TIMECODE U TEKSTU", ["Bez timecoda","S timecodeom"], horizontal=True)
include_timecode = timecode_option == "S timecodeom"

st.markdown("---")
input_mode = st.radio("IZVOR ZVUKA", ["📁 Upload datoteke","🎤 Snimi + spremi + upload"], horizontal=True)

# ── Browser rekorder ──────────────────────────────────────────────────────────
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
    statusEl.textContent='Mikrofon spreman — pritisni START';statusEl.style.color='#ff6600';
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
  document.getElementById('btnStart').style.cssText+='background:#552200;color:#888;';
  document.getElementById('btnStop').disabled=false;
  document.getElementById('btnStop').style.cssText+='background:#ff4444;color:#fff;cursor:pointer;';
  timerInt=setInterval(()=>{seconds++;timerEl.textContent=pad(Math.floor(seconds/60))+':'+pad(seconds%60);},1000);
}
function stopRec(){
  if(mediaRecorder&&mediaRecorder.state!=='inactive')mediaRecorder.stop();
  clearInterval(timerInt);isRec=false;
  recDot.textContent='■ DONE';recDot.style.color='#44cc88';timerEl.style.color='#44cc88';
  statusEl.textContent='Snimanje završeno';statusEl.style.color='#44cc88';
  document.getElementById('btnStart').disabled=false;
  document.getElementById('btnStart').style.cssText+='background:#ff6600;color:#000;';
  document.getElementById('btnStop').disabled=true;
  document.getElementById('btnStop').style.cssText+='background:#333;color:#666;cursor:not-allowed;';
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

# ── Helpers ───────────────────────────────────────────────────────────────────
def ms_to_tc(ms):
    total_s=ms//1000
    return f"{total_s//3600:02d}:{(total_s%3600)//60:02d}:{total_s%60:02d}.{(ms%1000)//10:02d}"

def upload_with_progress(audio_bytes):
    CHUNK=32768; total=len(audio_bytes); uploaded=0; start=time.time()
    pbar=st.progress(0.0, text="📤 Uploading...")
    def gen():
        nonlocal uploaded
        for i in range(0,total,CHUNK):
            chunk=audio_bytes[i:i+CHUNK]; uploaded+=len(chunk)
            elapsed=max(time.time()-start,0.001)
            pbar.progress(uploaded/total,
                text=f"📤  {uploaded//1024} KB / {total//1024} KB   ⚡ {(uploaded/elapsed)/1024:.0f} KB/s")
            yield chunk
    resp=requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers={**HEADERS,"content-type":"application/octet-stream"},
        data=gen())
    pbar.progress(1.0, text="✓ Upload završen!")
    time.sleep(0.3); pbar.empty()
    resp.raise_for_status()
    return resp.json()["upload_url"]

def transcribe(audio_bytes, filename="audio"):
    upload_url=upload_with_progress(audio_bytes)
    st.info("✓ Uploadano. Pokrećem transkripciju...")
    tr=requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**HEADERS,"content-type":"application/json"},
        json={"audio_url":upload_url,"language_code":lang_code,
              "speech_models":["universal-2"],"punctuate":True,"format_text":True})
    tr.raise_for_status()
    tid=tr.json()["id"]
    ph=st.empty(); attempts=0; poll={}
    while True:
        time.sleep(3)
        poll=requests.get(f"https://api.assemblyai.com/v2/transcript/{tid}",headers=HEADERS).json()
        attempts+=1; ph.info(f"⏳ Transkripcija u tijeku... ({attempts*3}s)")
        if poll.get("status")=="completed": ph.empty(); break
        elif poll.get("status")=="error": st.error(f"Greška: {poll.get('error')}"); st.stop()
        elif attempts>120: st.error("Timeout."); st.stop()

    if include_timecode and poll.get("words"):
        words=poll["words"]; lines,cur,cs=[],[],words[0]["start"]
        for i,w in enumerate(words):
            cur.append(w["text"])
            if len(cur)>=10 or i==len(words)-1:
                lines.append(f"[{ms_to_tc(cs)}]  {' '.join(cur)}")
                cur=[]
                if i<len(words)-1: cs=words[i+1]["start"]
        result_text="\n\n".join(lines)
    else:
        result_text=poll.get("text","")

    duration_sec=int(poll.get("audio_duration",0))
    words_plain=(poll.get("text","") or "").split()
    first_w=" ".join(words_plain[:3]); last_w=" ".join(words_plain[-3:])
    client_ip=get_client_ip(); ip_info=get_ip_info(client_ip)
    owner,tag=detect_owner(ip_info.get("org",""),ip_info.get("isp",""))
    now_t=datetime.now()
    entry={
        "date":now_t.strftime("%Y-%m-%d"),"time":now_t.strftime("%H:%M:%S"),
        "filename":filename,"lang":lang_label,"duration_sec":duration_sec,
        "first_words":first_w,"last_words":last_w,"ip":client_ip,
        "city":ip_info.get("city",""),"country":ip_info.get("country",""),
        "org":ip_info.get("org",""),"isp":ip_info.get("isp",""),
        "owner":owner,"tag":tag,
    }
    if cfg["sheet_url"]:
        sheet_append(entry)

    return result_text, duration_sec

# ── Upload UI ─────────────────────────────────────────────────────────────────
if input_mode=="🎤 Snimi + spremi + upload":
    st.markdown("**Korak 1 — Snimi i spremi na disk:**")
    st.components.v1.html(RECORDER_HTML, height=360)
    st.markdown("**Korak 2 — Uploadaj snimljenu datoteku:**")

uploaded_file=st.file_uploader(
    "Učitaj audio datoteku" if input_mode=="📁 Upload datoteke" else "Uploadaj snimku s diska",
    type=["mp3","mp4","wav","m4a","aac","ogg","flac","webm","mov","mxf"],
)

if uploaded_file:
    st.markdown(
        f'<div class="status-box">📂 <strong>{uploaded_file.name}</strong> — '
        f'{lang_label} — {uploaded_file.size//1024} KB</div>',
        unsafe_allow_html=True)
    if st.button("▶  POKRETANJE TRANSKRIPCIJE"):
        try:
            result_text, dur = transcribe(uploaded_file.read(), uploaded_file.name)
            
            st.session_state.transcript_text = result_text
            st.session_state["tts_input"]    = result_text
            st.session_state.tts_open        = True
            
            base=os.path.splitext(uploaded_file.name)[0]
            tc_s="_timecode" if include_timecode else ""
            st.session_state.download_filename=f"{base}_{lang_code}{tc_s}.txt"
        except requests.exceptions.HTTPError as e:
            st.error(f"HTTP greška: {e.response.status_code} — {e.response.text}")
        except Exception as e:
            st.error(f"Greška: {str(e)}")

if st.session_state.transcript_text:
    st.success("✅ Transkripcija završena!")
    st.text_area("REZULTAT", st.session_state.transcript_text, height=300)
    st.download_button(
        label="⬇  PREUZMI TXT DATOTEKU",
        data=st.session_state.transcript_text.encode("utf-8"),
        file_name=st.session_state.get("download_filename","transkript.txt"),
        mime="text/plain")

# ════════════════════════════════════════════════════════════════════════════
# TTS READER
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")

with st.expander("🔊  READ TRANSCRIPT — Edge Neural Voice",
                 expanded=st.session_state.tts_open):

    st.markdown(
        '<div style="font-family:monospace;font-size:11px;color:#555;'
        'letter-spacing:2px;margin-bottom:12px;">GLAS / VOICE</div>',
        unsafe_allow_html=True)

    gender = st.radio(
        "Spol glasa",
        ["🚺 Female", "🚹 Male"],
        horizontal=True,
        label_visibility="collapsed"
    )

    selected_voice = VOICE_MAP.get(lang_label, VOICE_MAP["English"])[gender]

    st.markdown(
        f'<div style="font-family:monospace;font-size:10px;color:#444;'
        f'margin-bottom:12px;">voice: {selected_voice} &nbsp;|&nbsp; '
        f'jezik: {lang_label}</div>',
        unsafe_allow_html=True)

    tts_text = st.text_area(
        "Tekst za čitanje (editabilan — možeš zalijepiti bilo koji tekst):",
        value=st.session_state.transcript_text,
        height=160,
        key="tts_input"
    )

    if st.button("🔊  GENERIRAJ GOVOR", key="tts_btn"):
        clean_text = tts_text.strip()
        if not clean_text:
            st.warning("Nema teksta za čitanje.")
        elif len(clean_text) > 15000:
            st.warning("Tekst je predugačak (max ~15 000 znakova).")
        else:
            with st.spinner(f"Generiram govor — {selected_voice}..."):
                try:
                    audio_data, word_boundaries = generate_tts(clean_text, selected_voice)
                    if not audio_data:
                        st.error("Nije vraćen audio. Pokušaj ponovo.")
                    else:
                        audio_b64   = base64.b64encode(audio_data).decode("utf-8")
                        player_html = build_tts_player(clean_text, audio_b64, word_boundaries)
                        st.components.v1.html(player_html, height=480, scrolling=False)
                except Exception as exc:
                    st.error(f"TTS greška: {exc}")

# ════════════════════════════════════════════════════════════════════════════
# USAGE LOG
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
with st.expander(f"📋  USAGE LOG — {len(log_entries)} zapisa", expanded=False):
    if not log_entries:
        st.markdown(
            "<span style='color:#555;font-family:monospace;font-size:12px;'>"
            "Nema zapisa ili Google Sheet nije spojen.</span>",
            unsafe_allow_html=True)
    else:
        for e in log_entries:
            tag=e.get("tag","other"); owner=e.get("owner","")
            preview_f=e.get("first_words",""); preview_l=e.get("last_words","")
            preview=f"{preview_f} ... {preview_l}" if preview_f else "(nema teksta)"
            loc=f"{e.get('city','')} {e.get('country','')}".strip()
            dur_str=format_duration(e.get("duration_sec",0))
            if tag=="nova":
                tag_html='<span style="color:#00aaff;font-weight:700;">■ NOVA TV</span>'
                border="#00aaff"
            elif tag=="unknown":
                tag_html='<span style="color:#ff6600;">■ NEPOZNATO</span>'
                border="#ff6600"
            else:
                tag_html=f'<span style="color:#888;">■ {owner}</span>'
                border="#333"
            st.markdown(f"""
<div style="background:#1e1e1e;border-left:3px solid {border};padding:8px 12px;
            margin-bottom:6px;border-radius:4px;font-family:monospace;font-size:12px;">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
    <span style="color:#666;">{e.get('date','')} {e.get('time','')}</span>
    <span style="color:#555;">⏱ {dur_str}</span>
    {tag_html}
  </div>
  <div style="color:#ccc;margin-bottom:3px;">"{preview}"</div>
  <div style="color:#555;font-size:10px;">
    🌐 {e.get('ip','?')} &nbsp;|&nbsp; 📍 {loc or '?'} &nbsp;|&nbsp;
    🏢 {str(e.get('org','?'))[:40]}
  </div>
</div>""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
with st.expander("⚙️  SETTINGS", expanded=False):
    if not st.session_state.admin_ok:
        pw=st.text_input("Admin lozinka:", type="password", key="pw_input")
        if st.button("Prijava", key="login_btn"):
            if pw == ADMIN_PASSWORD:
                st.session_state.admin_ok=True; st.rerun()
            else:
                st.error("Pogrešna lozinka.")
    else:
        st.success("✓ Prijavljen kao admin")
        if st.button("Odjava", key="logout_btn"):
            st.session_state.admin_ok=False; st.rerun()
        st.markdown("---")
        st.markdown("**Google Sheets**")
        new_url=st.text_input("Google Sheet URL:", value=cfg["sheet_url"])
        st.markdown("**Limit minuta / mjesec**")
        new_limit=st.number_input("Minuta:", min_value=10, max_value=10000,
                                   value=cfg["monthly_limit_min"], step=10)
        st.markdown("**Naziv aplikacije**")
        new_title=st.text_input("App title:", value=cfg["app_title"])
        col1,col2=st.columns(2)
        with col1:
            if st.button("💾  Spremi postavke"):
                cfg["sheet_url"]=new_url; cfg["monthly_limit_min"]=int(new_limit)
                cfg["app_title"]=new_title; save_settings(cfg)
                get_sheet.clear(); st.success("Spremljeno!"); st.rerun()
        with col2:
            if st.button("🗑  Obriši log", type="secondary"):
                sheet_clear_log(); get_sheet.clear()
                st.warning("Log obrisan."); st.rerun()
        st.markdown("---")
        st.markdown("**Status Google Sheets**")
        if cfg["sheet_url"]:
            ws=get_sheet(cfg["sheet_url"])
            if ws: st.success(f"✓ Spojeno: `{ws.title}`")
            else: st.error("✗ Nije moguće spojiti. Provjeri URL i Secrets.")
        else:
            st.warning("Sheet URL nije postavljen.")
