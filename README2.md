# Askria - AI Voice Assistant v2.5 (Dynamic App Search Edition) 🎙️🤖

**Askria** is an advanced **AI Voice Assistant** featuring a modern graphical interface and intelligent desktop automation capabilities, running on a fully local and hybrid architecture. It combines powerful open-source tools like `faster-whisper`, `Ollama`, `Piper TTS`, and `DuckDuckGo Search` to deliver a fast, fluid, and responsive voice and text-based assistant experience.

---

## 🌟 Key Features

- **🎙️ Advanced Speech Recognition (STT):** High-accuracy audio processing via `faster-whisper` with automatic silence detection (auto-stops after 5 seconds of silence or a maximum duration of 30 seconds).
- **🧠 Ollama Integration:** Full compatibility with your local LLM models (e.g., gemma, llama, etc.) with dynamic model switching and listing support.
- **🔊 Natural Text-to-Speech (TTS):** Fast and smooth voice responses powered by `Piper TTS`, which can be instantly interrupted when needed.
- **🚀 Dynamic App Search & Management:** 
  - Automatically indexes installed applications: Windows Registry + Start Menu/Desktop shortcuts (`.lnk`, `.url`, `.exe`) on Windows, `/Applications` bundles on macOS, and `.desktop` entries on Linux.
  - Open or close applications seamlessly using simple voice or text commands (`open: spotify` / `close: chrome`).
- **🔍 Web Search & Security (VirusTotal):** 
  - Real-time web search capabilities via `DuckDuckGo`.
  - Built-in VirusTotal API integration to automatically scan search result links for malware/suspicious activity, complete with an LED status indicator.
- **🎨 Modern Dark/Light UI:** Built with CustomTkinter, featuring a sleek modern window design, **Compact/Minimalist Mode**, and **Always on Top (Pin)** toggles.
- **📥 System Tray Support:** Runs in the background with full system tray control (`pystray` support).
- **⚙️ Easy Configuration:** Fully customizable via `.env` for API keys and model parameters.

---

## 🛠️ Tech Stack & Libraries

- **UI & Desktop:** `customtkinter`, `pystray`, `Pillow`
- **AI & Speech:** `faster-whisper`, `Ollama`, `Piper TTS`
- **Audio Processing:** `sounddevice`, `soundfile`, `numpy`
- **Search & Security:** `ddgs` (DuckDuckGo), `requests`

---

## ⚙️ Installation & Usage

> **Platform support:** Askria runs on **Windows, macOS, and Linux**. App search/launch adapts automatically: Windows Registry + Start Menu/Desktop shortcuts on Windows, `/Applications` scanning on macOS, and `.desktop` entries on Linux.

Follow these steps to run the application:

### 1. Prerequisites

**Ollama** — install from [ollama.com](https://ollama.com/download) and pull a model, e.g.:
```bash
ollama pull gemma3:12b
```

**Piper** (text-to-speech engine) — install the CLI via pip:
```bash
pip install piper-tts
```
This gives you the `piper` command that Askria calls under the hood. Make sure it's on your system PATH (a plain `pip install` inside a venv usually already takes care of this — test with `piper --help`).

> Note: active Piper development moved to [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0) after the original `rhasspy/piper` repo was archived. Check that license fits your use case if you plan to distribute Askria with Piper bundled.
>
> **License boundary:** Piper is an external, separately-installed program that Askria invokes via a plain command-line call (`subprocess.run(["piper", ...])`) — its code, binary, and voice model files are never copied into or shipped with this repository. Please keep it that way: don't vendor Piper's source, binary, or voice files into this repo, and don't `import` its Python package directly into Askria's own code, as that would change the licensing picture.

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Download the Piper voice model
Askria uses the `en_US-amy-medium` voice by default. You have two options:
- **Automatic:** just run `piper --model en_US-amy-medium --output_file test.wav` once — Piper downloads the voice files for you on first use.
- **Manual:** grab `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json` directly from the [Piper voices collection on Hugging Face](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium) and place both files in the project directory (or wherever your Piper `--data-dir` points).

Want a different voice/language? Browse the full list in the same Hugging Face collection and update `PIPER_MODEL` in your `.env` accordingly.

### 4. Configure the Environment
Copy `.env.example` to `.env` and fill in your own values:
```bash
cp .env.example .env
```
```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:12b
WHISPER_MODEL_SIZE=small
PIPER_MODEL=en_US-amy-medium.onnx
VT_API_KEY=your_virustotal_api_key_here_optional
```
`.env` is git-ignored — never commit your real API key.

### 5. Run the Application
```bash
python AskriaAssistant.py
```

---

## 🖥️ Usage Guide

- **Voice Input:** Click the microphone icon (`🎤 Speak`) and start talking. It will automatically process after 5 seconds of silence.
- **Text Input:** Type your message into the bottom text bar and press `Enter` or click the send button (`➤`).
- **App Control:** Speak or type commands like "Open Spotify" or "Close Chrome".
- **Compact / Minimalist Mode:** Click the square/compact icon (`⬛`) located in the top-right control panel (next to settings and pin buttons) to shrink the window into a mini minimalist view.

---

## 📜 License
This project is licensed under the terms of the MIT License. Check out the LICENSE file in the repository for more details.
