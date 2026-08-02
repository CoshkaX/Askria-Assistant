# -*- coding: utf-8 -*-
"""
Askria - AI Voice Assistant v2.5 (Dynamic App Search Edition)
---------------------------------
Microphone -> faster-whisper (STT) -> Ollama (LLM) -> Piper (TTS) -> Speaker

Features:
- Advanced App Search (Registry + Start Menu + Desktop LNK scanning)
- .env support for API keys and models
- Compact Mode (text box only, short replies)
- Auto silence detection - 5 seconds
- API connection status with VT LED indicator
- Dark modern UI
"""

import queue, threading, subprocess, warnings, base64, re, sys, os, json, time, traceback, shlex
from datetime import datetime

if sys.platform.startswith("win"):
    import winreg
else:
    winreg = None
from concurrent.futures import ThreadPoolExecutor, as_completed
warnings.filterwarnings("ignore")

import numpy as np, sounddevice as sd, soundfile as sf, requests
from ddgs import DDGS
from faster_whisper import WhisperModel
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw
import customtkinter as ctk
from dotenv import load_dotenv

# .env download the env file
load_dotenv()

# ================== SETTINGS ==================
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.03
SILENCE_THRESHOLD = 0.015
SILENCE_DURATION = 5.0
MAX_RECORD_DURATION = 30
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
OLLAMA_TIMEOUT = 300
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_DEVICE = "cpu"
WHISPER_CPU_THREADS = 0
PIPER_MODEL = os.getenv("PIPER_MODEL", "en_US-amy-medium.onnx")
VT_API_KEY = os.getenv("VT_API_KEY", "")
MAX_HISTORY_MESSAGES = 40
WINDOW_WIDTH = 380
WINDOW_HEIGHT = 620
COMPACT_HEIGHT = 150
SETTINGS_WIDTH = 300
MARGIN_X = 20
MARGIN_Y = 60
LOG_FILE = "askria_error_log.txt"

# Colors - (Light, Dark) tuples for CustomTkinter
C_BG = ("#f4f4f5", "#0a0a0f")
C_PANEL = ("#ffffff", "#141418")
C_PANEL_HOVER = ("#e8e8ec", "#1e1e24")
C_BORDER = ("#d4d4d8", "#2a2a32")
C_TEXT = ("#18181b", "#e8e8ec")
C_TITLE = ("#09090b", "#f0f0f5")
C_SUBTEXT = ("#71717a", "#a1a1aa")
C_ACCENT = ("#2563eb", "#6366f1")
C_ACCENT_HOVER = ("#1d4ed8", "#818cf8")
C_SUCCESS = ("#16a34a", "#22c55e")
C_WARNING = ("#ca8a04", "#eab308")
C_DANGER = ("#dc2626", "#ef4444")
C_DANGER_DARK = ("#991b1b", "#7f1d1d")
C_DANGER_LIGHT = ("#fee2e2", "#3f1010")
C_STOP_TEXT = ("#991b1b", "#fca5a5")

def log_error(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

# ============ API CHECK ============
def check_ollama():
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        if r.status_code == 200:
            return True, f"Connected ({len(r.json().get('models', []))} models)"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "No connection"
    except Exception as e:
        return False, str(e)

def check_vt():
    if not VT_API_KEY:
        return "missing", "Not configured"
    try:
        test_url = "https://www.google.com"
        url_id = base64.urlsafe_b64encode(test_url.encode()).decode().strip("=")
        r = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}",
                        headers={"x-apikey": VT_API_KEY}, timeout=10)
        if r.status_code == 200:
            return "ok", "Valid"
        elif r.status_code == 401:
            return "invalid", "Invalid key"
        elif r.status_code == 403:
            return "invalid", "Quota exceeded"
        else:
            return "invalid", f"HTTP {r.status_code}"
    except requests.exceptions.ConnectionError:
        return "invalid", "No internet"
    except Exception as e:
        return "invalid", str(e)

# ============ APP INDEXING & SEARCH ============
def fix_turkish(text):
    for old, new in {'ı':'i','İ':'I','ç':'c','Ç':'C','ğ':'g','Ğ':'G','ö':'o','Ö':'O','ş':'s','Ş':'S','ü':'u','Ü':'U'}.items():
        text = text.replace(old, new)
    return text

def _parse_desktop_file(path):
    """Parses a Linux .desktop file, returns (name, exec_cmd) or None (hidden/invalid entries)."""
    name, exec_cmd, no_display, in_entry = None, None, False, False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_entry = True
                    continue
                if line.startswith("[") and line != "[Desktop Entry]":
                    in_entry = False
                    continue
                if not in_entry or not line or line.startswith("#"):
                    continue
                if line.startswith("Name=") and name is None:
                    name = line.split("=", 1)[1].strip()
                elif line.startswith("Exec=") and exec_cmd is None:
                    exec_cmd = line.split("=", 1)[1].strip()
                elif line.startswith("NoDisplay="):
                    no_display = line.split("=", 1)[1].strip().lower() == "true"
    except Exception:
        return None
    if no_display or not name or not exec_cmd:
        return None
    exec_cmd = re.sub(r"%[a-zA-Z]", "", exec_cmd).strip()  # strip %U, %f, etc. field codes
    return name, exec_cmd

def build_app_index():
    """Scans the OS (Windows Registry/Start Menu, macOS /Applications, or Linux .desktop files) to build an app index."""
    apps = {}

    # ---- Windows: Registry App Paths + Start Menu / Desktop shortcuts ----
    if sys.platform.startswith("win"):
        apps.update({
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "cmd": "cmd.exe",
            "explorer": "explorer.exe",
            "spotify": "spotify.exe",
            "chrome": "chrome.exe",
        })

        registry_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
        ]
        for hive, subkey in registry_paths:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    for i in range(1024):
                        try:
                            app_key_name = winreg.EnumKey(key, i)
                            clean_name = app_key_name.lower().replace(".exe", "")
                            with winreg.OpenKey(key, app_key_name) as app_key:
                                app_path, _ = winreg.QueryValueEx(app_key, "")
                                if app_path and isinstance(app_path, str):
                                    apps[clean_name] = app_path
                        except OSError:
                            break
            except Exception:
                pass

        search_dirs = [
            os.path.join(os.environ.get('PROGRAMDATA', 'C:\\ProgramData'), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get('APPDATA', ''), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get('USERPROFILE', ''), r"Desktop"),
            os.path.join(os.environ.get('PUBLIC', 'C:\\Users\\Public'), r"Desktop")
        ]

        for d in search_dirs:
            if not os.path.exists(d): continue
            for root, dirs, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(('.lnk', '.url', '.exe')):
                        clean_name = os.path.splitext(f)[0].lower()
                        apps[clean_name] = os.path.join(root, f)

    # ---- macOS: /Applications, ~/Applications, /System/Applications ----
    elif sys.platform == "darwin":
        apps.update({
            "finder": "/System/Library/CoreServices/Finder.app",
            "terminal": "/System/Applications/Utilities/Terminal.app",
            "calculator": "/System/Applications/Calculator.app",
            "safari": "/Applications/Safari.app",
        })

        search_dirs = [
            "/Applications",
            "/System/Applications",
            "/System/Applications/Utilities",
            os.path.join(os.path.expanduser("~"), "Applications"),
        ]
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for entry in os.listdir(d):
                if entry.lower().endswith(".app"):
                    clean_name = os.path.splitext(entry)[0].lower()
                    apps[clean_name] = os.path.join(d, entry)

    # ---- Linux: .desktop entries (freedesktop.org standard) ----
    elif sys.platform.startswith("linux"):
        apps.update({
            "terminal": "x-terminal-emulator",
            "files": "xdg-open .",
        })

        search_dirs = [
            "/usr/share/applications",
            "/usr/local/share/applications",
            os.path.join(os.path.expanduser("~"), ".local/share/applications"),
        ]
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for root, dirs, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(".desktop"):
                        parsed = _parse_desktop_file(os.path.join(root, f))
                        if parsed:
                            disp_name, exec_cmd = parsed
                            apps[disp_name.lower()] = exec_cmd
                            clean_name = os.path.splitext(f)[0].lower()
                            apps.setdefault(clean_name, exec_cmd)

    return apps

ALLOWED_APPS = build_app_index()

def open_app(name):
    name = name.lower().strip()
    target_path = ALLOWED_APPS.get(name)

    # Tam eşleşme yoksa kısmi (içinde geçen) eşleşme ara (örn: "gta 5" için "Grand Theft Auto V")
    if not target_path:
        for app_name, app_path in ALLOWED_APPS.items():
            if name in app_name or (len(name) > 3 and app_name in name):
                target_path = app_path
                name = app_name
                break

    if not target_path:
        # Son çare: Doğrudan sistem PATH'inde çalışmayı dener
        target_path = name

    try:
        if sys.platform.startswith("win"):
            os.startfile(target_path)
        elif sys.platform == "darwin":
            if target_path.lower().endswith(".app"):
                subprocess.Popen(["open", target_path])
            else:
                subprocess.Popen(["open", "-a", target_path])
        else:  # Linux and other POSIX systems
            try:
                args = shlex.split(target_path)
            except ValueError:
                args = [target_path]
            subprocess.Popen(args)
        return f"OK: {name.title()} opened."
    except Exception as e:
        try:
            subprocess.Popen(target_path, shell=True)
            return f"OK: {name.title()} opened."
        except Exception as e2:
            return f"ERROR: Could not open '{name}': {e2}"

def close_app(name):
    name = name.lower().strip()
    target_path = ALLOWED_APPS.get(name)
    
    if not target_path:
        for app_name, app_path in ALLOWED_APPS.items():
            if name in app_name or (len(name) > 3 and app_name in name):
                target_path = app_path
                name = app_name
                break

    try:
        if sys.platform.startswith("win"):
            exe_name = f"{name.replace(' ', '')}.exe"
            if target_path and target_path.endswith('.exe'):
                exe_name = os.path.basename(target_path)
            subprocess.run(["taskkill", "/IM", exe_name, "/F"], check=False, capture_output=True)
            subprocess.run(["taskkill", "/IM", f"{name}.exe", "/F"], check=False, capture_output=True)
        elif sys.platform == "darwin":
            proc_name = name
            if target_path and target_path.lower().endswith(".app"):
                proc_name = os.path.splitext(os.path.basename(target_path))[0]
            subprocess.run(["killall", proc_name], check=False, capture_output=True)
        else:  # Linux and other POSIX systems
            proc_name = name
            if target_path:
                try:
                    proc_name = os.path.basename(shlex.split(target_path)[0])
                except (ValueError, IndexError):
                    pass
            subprocess.run(["pkill", "-f", proc_name], check=False, capture_output=True)
        return f"OK: Close signal sent for {name.title()}."
    except Exception as e:
        return f"ERROR: Could not close: {e}"

def vt_safe(url):
    if not VT_API_KEY:
        return True
    try:
        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        r = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}",
                        headers={"x-apikey": VT_API_KEY}, timeout=8)
        if r.status_code == 404:
            return True
        r.raise_for_status()
        stats = r.json()["data"]["attributes"]["last_analysis_stats"]
        return stats.get("malicious", 0) == 0 and stats.get("suspicious", 0) == 0
    except:
        return True

def web_search(query, max_results=5):
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results * 2))
        if not raw:
            return None
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(vt_safe, r.get("href", "")): r for r in raw if r.get("href")}
            safe = [futures[f] for f in as_completed(futures) if f.result()]
        order = {id(r): i for i, r in enumerate(raw)}
        safe.sort(key=lambda r: order.get(id(r), 999))
        safe = safe[:max_results]
        if not safe:
            return None
        return "\n\n".join([f"[{i+1}] {r.get('title','Untitled')}\n{r.get('body','')}\nSource: {r.get('href','')}" for i, r in enumerate(safe)])
    except Exception as e:
        return f"SEARCH_ERROR: {e}"

WEATHER_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "heavy thunderstorm with hail",
}

def get_weather(city):
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en"}, timeout=10).json().get("results")
        if not geo:
            return f"ERROR: Location not found for {city}."
        lat, lon, name = geo[0]["latitude"], geo[0]["longitude"], geo[0].get("name", city)
        w = requests.get("https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                    "timezone": "auto"}, timeout=10).json()["current"]
        return (f"Weather in {name}: {w['temperature_2m']}C (feels like {w['apparent_temperature']}C), "
                f"{WEATHER_CODES.get(w.get('weather_code'),'unknown')}, humidity {w['relative_humidity_2m']}%, "
                f"rain {w['precipitation']} mm, wind {w['wind_speed_10m']} km/h")
    except Exception as e:
        return f"ERROR: Could not get weather: {e}"

def handle_command(text):
    t = text.lower().strip()
    m = re.search(r"(?:open|close)[\s:]+([a-z0-9\s]+)", t)
    if m:
        action = "open" if "open" in t.split(":")[0] else "close"
        app = m.group(1).strip()
        return ("app", open_app(app) if action == "open" else close_app(app))
    
    m = re.search(r"(?:weather)[\s:]+([a-z\s]+)", t)
    if m:
        return ("weather", get_weather(m.group(1).strip()))
    
    m = re.search(r"(?:search|find|look up|google)[\s:]+(.+)", t)
    if m:
        return ("search", web_search(m.group(1).strip()))
    return None

whisper_model = None
compact_mode = False

def get_system_prompt(compact=False):
    base = ("You are a voice assistant named Askria. If the user wants to open/close ANY app or game (e.g. gta 5, chrome, spotify), "
            "ONLY reply with: open:app_name or close:app_name. Do not add any other text. "
            "For weather, ONLY reply with: weather:city_name. "
            "For current info/news/research, ONLY reply with: search:query. For anything else, reply naturally and fluently in conversational language.")
    return base + ("\n\nCOMPACT MODE: Keep answers as SHORT and CONCISE as possible. One sentence max." if compact else "\n\nNormal mode: You may give detailed and comprehensive answers.")

def build_history(compact=False):
    return [{"role": "system", "content": get_system_prompt(compact)}]

history = build_history(compact=False)

def transcribe(audio):
    if audio.size == 0 or whisper_model is None:
        return ""
    return " ".join(seg.text for seg in whisper_model.transcribe(audio, language="tr", vad_filter=True)[0]).strip()

def trim_history():
    if len(history) > MAX_HISTORY_MESSAGES + 1:
        history[:] = [history[0]] + history[-MAX_HISTORY_MESSAGES:]

def get_ollama_models():
    return [m.get("model") or m.get("name") for m in requests.get(OLLAMA_TAGS_URL, timeout=10).json().get("models", []) if m.get("model") or m.get("name")]

def shorten(name, ml=26):
    return name if len(name) <= ml else name[:ml-1] + "..."

def ask_ollama(user_text, compact=False):
    history.append({"role": "user", "content": user_text})
    trim_history()
    try:
        r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "messages": history, "stream": False,
                                           "options": {"num_predict": 80 if compact else -1}}, timeout=OLLAMA_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        history.pop()
        raise RuntimeError(f"Ollama error: {e}")
    reply = r.json()["message"]["content"]
    history.append({"role": "assistant", "content": reply})
    trim_history()
    return reply

def speak(text):
    try:
        subprocess.run(["piper", "--model", PIPER_MODEL, "--output_file", "reply.wav"],
                      input=fix_turkish(text).encode("utf-8"), check=True, capture_output=True)
        d, sr = sf.read("reply.wav", dtype="float32")
        sd.play(d, sr)
        sd.wait()
    except Exception as e:
        log_error(f"TTS: {e}")

# ================== GUI ==================
class AskriaGUI:
    def __init__(self):
        try:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("dark-blue")
            self.root = ctk.CTk()
            self.root.title("Askria")
            self.root.overrideredirect(True)
            tc = "#000001"
            self.root.configure(fg_color=tc)
            if sys.platform.startswith("win"):
                self.root.attributes("-transparentcolor", tc)
            self.current_width, self.current_height, self.settings_width = WINDOW_WIDTH, WINDOW_HEIGHT, SETTINGS_WIDTH
            self.settings_open, self.compact_mode = False, False
            self._center_window()
            self.recording, self.audio_frames, self.stream = False, [], None
            self.busy, self.speaking, self.pinned = False, False, False
            self._drag_x, self._drag_y = 0, 0
            self._model_display_map, self._models_loaded = {}, False
            self._record_start_time, self._silence_start = 0, None
            self.ollama_status, self.vt_status = (False, "Not checked"), ("missing", "Not configured")
            self._vt_blink_id = None
            self._build_ui()
            self._build_tray_icon()
            threading.Thread(target=self._check_apis_startup, daemon=True).start()
            self._refresh_models()
        except Exception as e:
            log_error(f"Init: {traceback.format_exc()}")
            raise

    def _center_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{self.current_width}x{self.current_height}+{sw-self.current_width-MARGIN_X}+{sh-self.current_height-MARGIN_Y}")

    def _build_ui(self):
        self.main_container = ctk.CTkFrame(self.root, fg_color=C_BG, corner_radius=20, border_width=1, border_color=C_BORDER)
        self.main_container.pack(fill="both", expand=True)

        # TOP BAR
        self.top_bar = ctk.CTkFrame(self.main_container, fg_color=C_PANEL, corner_radius=16, height=44)
        self.top_bar.pack(fill="x", padx=12, pady=(12, 6))
        self.top_bar.bind("<Button-1>", self._start_move)
        self.top_bar.bind("<B1-Motion>", self._do_move)
        self.top_bar.pack_propagate(False)

        logo = ctk.CTkLabel(self.top_bar, text="◉", font=("Segoe UI", 16), text_color=C_ACCENT, width=30, height=30)
        logo.pack(side="left", padx=(12, 4))
        logo.bind("<Button-1>", self._start_move)
        logo.bind("<B1-Motion>", self._do_move)

        self.title_label = ctk.CTkLabel(self.top_bar, text="Askria", font=("Segoe UI", 14, "bold"), text_color=C_TITLE)
        self.title_label.pack(side="left")
        self.title_label.bind("<Button-1>", self._start_move)
        self.title_label.bind("<B1-Motion>", self._do_move)

        self.subtitle_label = ctk.CTkLabel(self.top_bar, text="AI Assistant", font=("Segoe UI", 9), text_color=C_SUBTEXT)
        self.subtitle_label.pack(side="left", padx=(6, 10))
        self.subtitle_label.bind("<Button-1>", self._start_move)
        self.subtitle_label.bind("<B1-Motion>", self._do_move)

        def mkbtn(parent, text, cmd, hover=None, tc=None):
            return ctk.CTkButton(parent, text=text, width=32, height=32, fg_color="transparent",
                                hover_color=hover or C_PANEL_HOVER, text_color=tc or C_SUBTEXT,
                                font=("Segoe UI", 13), command=cmd, corner_radius=10)

        self.clear_btn = mkbtn(self.top_bar, "🗑", self._clear_chat)
        self.clear_btn.pack(side="right", padx=1)
        self.pin_btn = mkbtn(self.top_bar, "📌", self._toggle_pin)
        self.pin_btn.pack(side="right", padx=1)
        self.compact_btn = mkbtn(self.top_bar, "⬛", self._toggle_compact, tc=C_ACCENT)
        self.compact_btn.pack(side="right", padx=1)
        self.settings_btn = mkbtn(self.top_bar, "⚙️", self._toggle_settings)
        self.settings_btn.pack(side="right", padx=1)
        mkbtn(self.top_bar, "✕", self._hide_to_tray, C_DANGER).pack(side="right", padx=(4, 1))

        # CONTENT
        self.content_area = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.content_area.pack(fill="both", expand=True)
        self.chat_container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        self.chat_container.pack(side="right", fill="both", expand=True)

        # MODEL ROW
        self.model_row = ctk.CTkFrame(self.chat_container, fg_color=C_PANEL, corner_radius=14, height=36)
        self.model_row.pack(fill="x", padx=12, pady=(0, 6))
        self.model_row.pack_propagate(False)
        ctk.CTkLabel(self.model_row, text="🧠", font=("Segoe UI", 14), text_color=C_SUBTEXT).pack(side="left", padx=(12, 6))
        self.model_menu = ctk.CTkOptionMenu(self.model_row, values=["Loading..."], command=self._on_model_selected,
            fg_color=C_BORDER, button_color=C_BORDER, button_hover_color=C_ACCENT, text_color=C_TEXT,
            font=("Segoe UI", 11), height=28, corner_radius=10, state="disabled",
            dropdown_fg_color=C_PANEL, dropdown_hover_color=C_PANEL_HOVER, dropdown_text_color=C_TEXT)
        self.model_menu.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)
        self.model_refresh_btn = ctk.CTkButton(self.model_row, text="🔄", width=28, height=28, corner_radius=10,
            fg_color="transparent", hover_color=C_PANEL_HOVER, text_color=C_SUBTEXT, font=("Segoe UI", 12),
            command=self._refresh_models)
        self.model_refresh_btn.pack(side="right", padx=(0, 8))

        # STATUS
        self.status_frame = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        self.status_frame.pack(fill="x", padx=12, pady=(0, 6))
        self.status_icon = ctk.CTkLabel(self.status_frame, text="●", font=("Segoe UI", 10), text_color=C_SUCCESS)
        self.status_icon.pack(side="left", padx=(4, 4))
        self.status_label = ctk.CTkLabel(self.status_frame, text="Checking APIs...", font=("Segoe UI", 10), text_color=C_SUBTEXT)
        self.status_label.pack(side="left")
        self.vu_meter = ctk.CTkProgressBar(self.status_frame, width=70, height=5, corner_radius=3,
                                          progress_color=C_ACCENT, fg_color=C_BORDER)
        self.vu_meter.pack(side="right", padx=6)
        self.vu_meter.set(0)

        # CHAT BOX
        self.chat_box = ctk.CTkTextbox(self.chat_container, fg_color=C_PANEL, text_color=C_TEXT, font=("Segoe UI", 12),
            corner_radius=16, border_width=1, border_color=C_BORDER, wrap="word", activate_scrollbars=True,
            scrollbar_button_color=C_BORDER, scrollbar_button_hover_color=C_ACCENT)
        self.chat_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.chat_box.configure(state="disabled")

        # BOTTOM
        bottom = ctk.CTkFrame(self.chat_container, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(0, 14))

        entry_frame = ctk.CTkFrame(bottom, fg_color=C_PANEL, corner_radius=14, height=42)
        entry_frame.pack(fill="x", pady=(0, 8))
        entry_frame.pack_propagate(False)
        self.text_entry = ctk.CTkEntry(entry_frame, placeholder_text="Type a message...", fg_color="transparent",
            border_width=0, text_color=C_TEXT, height=42, font=("Segoe UI", 12))
        self.text_entry.pack(side="left", fill="x", expand=True, padx=(14, 6))
        self.text_entry.bind("<Return>", lambda e: self._send_text())
        self.send_btn = ctk.CTkButton(entry_frame, text="➤", width=36, height=36, corner_radius=10,
            fg_color=C_ACCENT, hover_color=C_ACCENT_HOVER, text_color="white", font=("Segoe UI", 16, "bold"),
            command=self._send_text)
        self.send_btn.pack(side="right", padx=(0, 6), pady=3)

        action_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        action_frame.pack(fill="x")
        self.mic_btn = ctk.CTkButton(action_frame, text="🎤  Speak", font=("Segoe UI", 13, "bold"),
            fg_color=C_BORDER, hover_color=C_ACCENT, text_color=C_TEXT, border_width=0, height=44, corner_radius=22,
            command=self._toggle_recording)
        self.mic_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_speak_btn = ctk.CTkButton(action_frame, text="🔇  Stop", width=110, font=("Segoe UI", 12, "bold"),
            fg_color=C_DANGER_LIGHT, hover_color=C_DANGER, text_color=C_STOP_TEXT, border_width=0, height=44, corner_radius=22,
            state="disabled", command=self._stop_speaking)
        self.stop_speak_btn.pack(side="right")

        # SETTINGS PANEL
        self.settings_container = ctk.CTkFrame(self.content_area, fg_color=C_PANEL, corner_radius=16,
                                               border_width=1, border_color=C_BORDER)
        ctk.CTkLabel(self.settings_container, text="⚙️  Settings", font=("Segoe UI", 16, "bold"), text_color=C_TITLE).pack(pady=(18, 12))

        def card(parent, title):
            c = ctk.CTkFrame(parent, fg_color=C_BG, corner_radius=12)
            c.pack(fill="x", padx=16, pady=(0, 12))
            ctk.CTkLabel(c, text=title, text_color=C_SUBTEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 8))
            return c

        # API Status card with VT LED
        ac = card(self.settings_container, "🔗 API Status")

        # VT LED row
        vt_row = ctk.CTkFrame(ac, fg_color="transparent")
        vt_row.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(vt_row, text="VirusTotal:", font=("Segoe UI", 10), text_color=C_SUBTEXT).pack(side="left")
        self.vt_led = ctk.CTkLabel(vt_row, text="●", font=("Segoe UI", 14), text_color=C_DANGER)
        self.vt_led.pack(side="left", padx=(6, 4))
        self.vt_led_label = ctk.CTkLabel(vt_row, text="Not configured", font=("Segoe UI", 10), text_color=C_SUBTEXT)
        self.vt_led_label.pack(side="left")

        # Ollama status row
        ol_row = ctk.CTkFrame(ac, fg_color="transparent")
        ol_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(ol_row, text="Ollama:", font=("Segoe UI", 10), text_color=C_SUBTEXT).pack(side="left")
        self.ol_led = ctk.CTkLabel(ol_row, text="●", font=("Segoe UI", 14), text_color=C_DANGER)
        self.ol_led.pack(side="left", padx=(6, 4))
        self.ol_led_label = ctk.CTkLabel(ol_row, text="Checking...", font=("Segoe UI", 10), text_color=C_SUBTEXT)
        self.ol_led_label.pack(side="left")

        self.api_check_btn = ctk.CTkButton(ac, text="🔄  Refresh", fg_color=C_BORDER, hover_color=C_ACCENT,
            text_color=C_TEXT, height=32, corner_radius=10, font=("Segoe UI", 11), command=self._check_apis_manual)
        self.api_check_btn.pack(fill="x", padx=12, pady=(0, 10))

        # Theme card
        tc = card(self.settings_container, "🎨  Appearance")
        self.theme_seg = ctk.CTkSegmentedButton(tc, values=["Dark", "Light"], command=self._change_theme,
            selected_color=C_ACCENT, unselected_color=C_BORDER, text_color=C_TEXT, font=("Segoe UI", 11), height=32)
        self.theme_seg.set("Dark")
        self.theme_seg.pack(fill="x", padx=12, pady=(0, 10))

        # Shortcuts card - DYNAMIC
        apps_c = card(self.settings_container, "🚀  Shortcuts")

        # Create a scrollable frame for apps list
        apps_frame = ctk.CTkScrollableFrame(apps_c, fg_color="transparent", height=120, 
            scrollbar_button_color=C_BORDER, scrollbar_button_hover_color=C_ACCENT)
        apps_frame.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkLabel(apps_frame, text=f"Found {len(ALLOWED_APPS)} apps. Showing top 30:", font=("Segoe UI", 10, "bold"), 
            text_color=C_TEXT).pack(anchor="w", pady=(0, 6))

        display_apps = list(ALLOWED_APPS.keys())[:30]
        for app_name in display_apps:
            ctk.CTkLabel(apps_frame, text=f"  • {app_name.title()}", font=("Segoe UI", 10), 
                text_color=C_SUBTEXT).pack(anchor="w", pady=(1, 1))

    def _update_vt_led(self):
        """Update VT LED based on status"""
        status, msg = self.vt_status

        # Cancel any existing blink
        if self._vt_blink_id is not None:
            self.root.after_cancel(self._vt_blink_id)
            self._vt_blink_id = None

        if status == "ok":
            self.vt_led.configure(text_color=C_SUCCESS)
            self.vt_led_label.configure(text=f"Connected - {msg}")
        elif status == "invalid":
            self.vt_led.configure(text_color=C_WARNING)
            self.vt_led_label.configure(text=f"Error - {msg}")
            self._blink_vt_led()
        else:  # missing
            self.vt_led.configure(text_color=C_DANGER)
            self.vt_led_label.configure(text="Not configured")

    def _blink_vt_led(self):
        """Blink yellow VT LED"""
        current = self.vt_led.cget("text_color")
        # Toggle between warning color and dark/off color
        new_color = C_WARNING if current == C_BORDER else C_BORDER
        self.vt_led.configure(text_color=new_color)
        self._vt_blink_id = self.root.after(600, self._blink_vt_led)

    def _update_ol_led(self):
        """Update Ollama LED"""
        ok, msg = self.ollama_status
        if ok:
            self.ol_led.configure(text_color=C_SUCCESS)
            self.ol_led_label.configure(text=f"Connected - {msg}")
            self.status_icon.configure(text_color=C_SUCCESS)
        else:
            self.ol_led.configure(text_color=C_DANGER)
            self.ol_led_label.configure(text=f"Error - {msg}")
            self.status_icon.configure(text_color=C_DANGER)

    def _check_apis_startup(self):
        try:
            self.ollama_status = check_ollama()
            self.vt_status = check_vt()
            self.root.after(0, self._update_vt_led)
            self.root.after(0, self._update_ol_led)

            oo, om = self.ollama_status
            parts = []
            if oo:
                parts.append(f"Ollama: {om}")
            else:
                parts.append(f"Ollama error: {om}")

            vs, vm = self.vt_status
            if vs == "ok":
                parts.append(f"VT: {vm}")
            elif vs == "invalid":
                parts.append(f"VT error: {vm}")
            else:
                parts.append("VT: Not configured")

            self.root.after(0, lambda: self._set_status("  |  ".join(parts)))
        except Exception as e:
            log_error(f"API startup: {e}")
            self.root.after(0, lambda: self._set_status("⚠️ API check failed"))

    def _check_apis_manual(self):
        self.api_check_btn.configure(state="disabled", text="⏳  Checking...")
        self._set_status("🔗 Checking APIs...")
        threading.Thread(target=self._check_apis_thread, daemon=True).start()

    def _check_apis_thread(self):
        try:
            self.ollama_status = check_ollama()
            self.vt_status = check_vt()
            self.root.after(0, self._update_vt_led)
            self.root.after(0, self._update_ol_led)
            self.root.after(0, lambda: self.api_check_btn.configure(state="normal", text="🔄  Refresh"))
            oo, _ = self.ollama_status
            self.root.after(0, lambda: self._set_status("✅ All APIs ready." if oo else "⚠️ Ollama not connected!"))
        except Exception as e:
            log_error(f"API manual: {e}")

    def _toggle_compact(self):
        global compact_mode, history
        self.compact_mode = not self.compact_mode
        compact_mode = self.compact_mode
        ctw = self.current_width + (self.settings_width if self.settings_open else 0)
        crx, cby = self.root.winfo_x() + ctw, self.root.winfo_y() + self.root.winfo_height()
        if self.compact_mode:
            for w in [self.chat_box, self.model_row, self.status_frame]:
                w.pack_forget()
            if self.settings_open:
                self.settings_container.pack_forget()
                self.settings_open = False
                self.settings_btn.configure(text_color=C_SUBTEXT)
            self.root.geometry(f"{self.current_width}x{COMPACT_HEIGHT}+{crx-self.current_width}+{cby-COMPACT_HEIGHT}")
            self.compact_btn.configure(text="⬜", text_color=C_ACCENT)
            self.subtitle_label.configure(text="Compact")
            self._set_status("📦 Compact mode active.")
            history = build_history(compact=True)
        else:
            self.model_row.pack(fill="x", padx=12, pady=(0, 6))
            self.status_frame.pack(fill="x", padx=12, pady=(0, 6))
            self.chat_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
            self.root.geometry(f"{self.current_width}x{self.current_height}+{crx-self.current_width}+{cby-self.current_height}")
            self.compact_btn.configure(text="⬛", text_color=C_SUBTEXT)
            self.subtitle_label.configure(text="AI Assistant")
            self._set_status("🤖 Normal mode. Ready.")
            history = build_history(compact=False)

    def _toggle_settings(self):
        ctw = self.current_width + (self.settings_width if self.settings_open else 0)
        crx, cby = self.root.winfo_x() + ctw, self.root.winfo_y() + self.root.winfo_height()
        if self.settings_open:
            self.settings_container.pack_forget()
            new_w = self.current_width
            new_h = COMPACT_HEIGHT if self.compact_mode else self.current_height
            self.root.geometry(f"{new_w}x{new_h}+{crx-new_w}+{cby-new_h}")
            self.settings_btn.configure(text_color=C_SUBTEXT)
            self.settings_open = False
        else:
            if self.compact_mode:
                self.model_row.pack(fill="x", padx=12, pady=(0, 6))
                self.status_frame.pack(fill="x", padx=12, pady=(0, 6))
                self.chat_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
            self.settings_container.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 14))
            new_w = self.current_width + self.settings_width
            self.root.geometry(f"{new_w}x{self.current_height}+{crx-new_w}+{cby-self.current_height}")
            self.settings_btn.configure(text_color=C_TITLE)
            self.settings_open = True

    def _change_theme(self, new_theme):
        ctk.set_appearance_mode(new_theme)

    def _start_move(self, event):
        self._drag_x, self._drag_y = event.x, event.y

    def _do_move(self, event):
        self.root.geometry(f"+{self.root.winfo_x()+event.x-self._drag_x}+{self.root.winfo_y()+event.y-self._drag_y}")

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        self.pin_btn.configure(text_color=C_WARNING if self.pinned else C_SUBTEXT)
        self._set_status("📌 Always on top." if self.pinned else "🤖 Ready.")

    def _clear_chat(self):
        if self.busy:
            return
        sm = history[0]
        history.clear()
        history.append(sm)
        self.chat_box.configure(state="normal")
        self.chat_box.delete("1.0", "end")
        self.chat_box.configure(state="disabled")
        self._set_status("🧹 Chat cleared.")

    def _refresh_models(self):
        self.model_refresh_btn.configure(state="disabled")
        self.model_menu.configure(state="disabled")
        self._set_status("🧠 Loading models...")
        threading.Thread(target=self._load_models_thread, daemon=True).start()

    def _load_models_thread(self):
        try:
            m = get_ollama_models()
            self.root.after(0, lambda: self._on_models_loaded(m, None))
        except Exception as e:
            self.root.after(0, lambda: self._on_models_loaded([], str(e)))

    def _on_models_loaded(self, models, error):
        global OLLAMA_MODEL
        self.model_refresh_btn.configure(state="normal")
        if error or not models:
            self._models_loaded = False
            self.model_menu.configure(values=["❌ No models found"], state="disabled")
            self.model_menu.set("❌ No models found")
            self._set_status(f"⚠️ {error}" if error else "⚠️ No models loaded.")
            return
        self._model_display_map, displays = {}, []
        for ad in models:
            disp = shorten(ad)
            bd, i = disp, 2
            while disp in self._model_display_map:
                disp = f"{bd} ({i})"
                i += 1
            self._model_display_map[disp] = ad
            displays.append(disp)
        self._models_loaded = True
        self.model_menu.configure(values=displays, state="normal")
        sdisp = next((d for d, a in self._model_display_map.items() if a == OLLAMA_MODEL), None)
        if sdisp is None:
            sdisp = displays[0]
            OLLAMA_MODEL = self._model_display_map[sdisp]
        self._set_status(f"✅ {len(models)} models found.")
        self.model_menu.set(sdisp)

    def _on_model_selected(self, sdisp):
        global OLLAMA_MODEL
        ad = self._model_display_map.get(sdisp)
        if not ad or ad == OLLAMA_MODEL:
            return
        OLLAMA_MODEL = ad
        self._set_status(f"🧠 Model: {OLLAMA_MODEL}")

    def _restore_model_controls(self):
        self.model_refresh_btn.configure(state="normal")
        self.model_menu.configure(state="normal" if self._models_loaded else "disabled")

    def _build_tray_icon(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill="#4f46e5")
        d.ellipse((24, 14, 40, 36), fill="#0a0a0f")
        d.rectangle((28, 34, 36, 46), fill="#0a0a0f")
        self.tray_icon = Icon("askria", img, "Askria - AI Assistant", menu=Menu(
            MenuItem("Show", self._show_from_tray, default=True),
            MenuItem("Exit", self._quit_app)))
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _hide_to_tray(self):
        self.root.withdraw()

    def _show_from_tray(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        if not self.pinned:
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", self.pinned))

    def _quit_app(self, icon=None, item=None):
        try:
            self.tray_icon.stop()
        except:
            pass
        self.root.after(0, self._destroy_all)

    def _destroy_all(self):
        if self._vt_blink_id is not None:
            self.root.after_cancel(self._vt_blink_id)
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
        self.root.quit()
        self.root.destroy()

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_label.configure(text=text))

    def _update_vu(self, amp):
        self.root.after(0, lambda: self.vu_meter.set(min(1.0, amp / 0.1)))

    def _append_chat(self, who, text):
        def _do():
            self.chat_box.configure(state="normal")
            ts = datetime.now().strftime("%H:%M")
            if who == "You":
                tag, prefix, color = "user", f"👤  [{ts}] You", C_ACCENT
            elif who == "Askria":
                tag, prefix, color = "assistant", f"🤖  [{ts}] Askria", C_SUCCESS
            else:
                tag, prefix, color = "error", f"⚠️  [{ts}] Error", C_DANGER
            self.chat_box.insert("end", f"{prefix}\n{text}\n\n", tag)
            self.chat_box.tag_config(tag, foreground=color)
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        self.root.after(0, _do)

    def _toggle_recording(self):
        if self.busy:
            return
        if self.recording:
            self._stop_recording()
            return
        global whisper_model
        if whisper_model is None:
            self.mic_btn.configure(state="disabled")
            self._set_status("🧠 Loading model...")
            threading.Thread(target=self._load_whisper_then_record, daemon=True).start()
        else:
            self._start_recording()

    def _load_whisper_then_record(self):
        global whisper_model
        try:
            whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type="int8",
                                         cpu_threads=WHISPER_CPU_THREADS)
            self.root.after(0, self._finish_loading_and_record)
        except Exception as e:
            log_error(f"Whisper: {e}")
            self.root.after(0, lambda: self._set_status(f"❌ {e}"))
            self.root.after(0, lambda: self.mic_btn.configure(state="normal"))

    def _finish_loading_and_record(self):
        self.mic_btn.configure(state="normal")
        self._start_recording()

    def _start_recording(self):
        self.recording, self.audio_frames = True, []
        self._record_start_time, self._silence_start = time.time(), None
        self.mic_btn.configure(text="⏹  Stop", fg_color=C_DANGER, hover_color="#dc2626", border_color="#b91c1c")
        self.send_btn.configure(state="disabled")
        self.model_menu.configure(state="disabled")
        self.model_refresh_btn.configure(state="disabled")
        self._set_status(f"🔴 Listening... ({int(SILENCE_DURATION)}s silence = auto stop)")

        def cb(indata, frames, t, status):
            self.audio_frames.append(indata.copy())
            amp = np.sqrt(np.mean(indata ** 2))
            self._update_vu(amp)
            if amp < SILENCE_THRESHOLD:
                if self._silence_start is None:
                    self._silence_start = time.time()
                elif time.time() - self._silence_start > SILENCE_DURATION and self.recording:
                    self.root.after(0, self._stop_recording)
            else:
                self._silence_start = None
            if time.time() - self._record_start_time > MAX_RECORD_DURATION and self.recording:
                self.root.after(0, self._stop_recording)

        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=cb,
                                     blocksize=int(SAMPLE_RATE * CHUNK_DURATION))
        self.stream.start()

    def _stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None
        self._update_vu(0)
        self.mic_btn.configure(text="🎤  Speak", fg_color=C_BORDER, hover_color=C_ACCENT, border_color=C_BORDER,
                               state="disabled")
        self._set_status("⏳ Processing...")
        self.busy = True
        audio = np.concatenate(self.audio_frames, axis=0).flatten() if self.audio_frames else np.array([], dtype="float32")
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio):
        try:
            text = transcribe(audio)
            if not text:
                self._set_status("⚠️ Could not understand, please try again.")
                return
            self._append_chat("You", text)
            self._handle_reply(text)
        except Exception as e:
            log_error(f"Process: {e}")
            self._append_chat("Error", str(e))
            self._set_status("🤖 Ready.")
        finally:
            self.busy = False
            self.root.after(0, lambda: self.mic_btn.configure(state="normal"))
            self.root.after(0, lambda: self.send_btn.configure(state="normal"))
            self.root.after(0, self._restore_model_controls)

    def _send_text(self):
        if self.busy:
            return
        text = self.text_entry.get().strip()
        if not text:
            return
        self.text_entry.delete(0, "end")
        self.busy = True
        self.mic_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self.model_menu.configure(state="disabled")
        self.model_refresh_btn.configure(state="disabled")
        self._append_chat("You", text)
        threading.Thread(target=self._process_text, args=(text,), daemon=True).start()

    def _process_text(self, text):
        try:
            self._handle_reply(text)
        except Exception as e:
            log_error(f"Text: {e}")
            self._append_chat("Error", str(e))
            self._set_status("🤖 Ready.")
        finally:
            self.busy = False
            self.root.after(0, lambda: self.mic_btn.configure(state="normal"))
            self.root.after(0, lambda: self.send_btn.configure(state="normal"))
            self.root.after(0, self._restore_model_controls)

    def _handle_reply(self, text):
        self._set_status("🤔 Thinking...")
        reply = ask_ollama(text, compact=self.compact_mode)
        cmd = handle_command(reply)
        if cmd:
            typ, result = cmd
            if typ == "search":
                if result is None:
                    reply = "🔍 No search results found."
                elif isinstance(result, str) and result.startswith("SEARCH_ERROR"):
                    reply = f"❌ Search error: {result.replace('SEARCH_ERROR: ', '')}"
                else:
                    self._set_status("🔎 Processing search results...")
                    reply = ask_ollama(
                        f"User asked: '{text}'\n\nUsing the search results below, give a DIRECT, DETAILED answer. "
                        f"Do not say 'the internet says'. Summarize in bullet points:\n\n{result}",
                        compact=self.compact_mode)
            else:
                reply = result
        self._append_chat("Askria", reply)
        self._set_status("🔊 Speaking...")
        self._set_speaking(True)
        try:
            speak(reply)
        finally:
            self._set_speaking(False)
        self._set_status("🤖 Ready. Press the mic button to speak.")

    def _set_speaking(self, sp):
        self.speaking = sp
        def _do():
            self.stop_speak_btn.configure(
                state="normal" if sp else "disabled",
                text_color="white" if sp else C_STOP_TEXT,
                fg_color=C_DANGER if sp else C_DANGER_LIGHT)
        self.root.after(0, _do)

    def _stop_speaking(self):
        if not self.speaking:
            return
        try:
            sd.stop()
        except:
            pass
        self._set_status("⏹ Speech stopped.")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    try:
        app = AskriaGUI()
        app.run()
    except Exception as e:
        log_error(f"FATAL: {traceback.format_exc()}")
        print(f"ERROR: {e}\nDetails: {LOG_FILE}")
        input("Press Enter to exit...")