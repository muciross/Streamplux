[github_description_readme_streamplux.md](https://github.com/user-attachments/files/35304257/github_description_readme_streamplux.md)


# 📡 StreamPlux

**StreamPlux** is a lightweight, standalone desktop application for Windows designed for ultra-low-latency screen and game streaming. Broadcast in **Full HD (1080p) at 60 FPS** with shared system audio—while your friends watch directly in their web browser via a single invite link, **with zero downloads or installations required on their end**.

---

## ✨ Key Features

- **⚡ Ultra-Lightweight & Eco-CPU Engine:** Built as a native desktop app (no heavy browser or Electron overhead). Uses hardware-friendly C-backed `libswscale` SIMD color conversion, H.264 priority, and automatic standby throttling (drops to 8 FPS when 0 viewers are connected) to keep CPU usage minimal while gaming.
- **🎮 Flexible Screen & Game Capture:** Stream your **entire monitor** (multi-monitor supported) or lock onto a **single game/application window**—automatically tracking the window even if you move it.
- **🎬 Crisp Video Quality:** Supports **1080p (Full HD)**, **720p (HD)**, **480p**, or **Native Resolution** at **60 FPS**, **30 FPS**, or **15 FPS**, plus an optional **Eco-CPU Mode** for CPU-intensive games.
- **🔊 System Audio & Mic Mixing:** Captures desktop/game audio natively via WASAPI Loopback with a real-time VU meter, plus optional microphone mixing so viewers hear both your game and your voice.
- **🌐 Zero-Install Browser Viewing + 1-Click Public Link:**
  - Instant **LAN invite link** for local network streaming.
  - Built-in **1-click Public Link generator** (via Cloudflare Tunnel) that creates an instant `https://` invite URL for friends over the internet—no port forwarding, no accounts, and no app required for viewers.
- **💬 Built-in Live Chat & Telemetry:** Real-time WebSocket chat alongside the browser player and live WebRTC stats (resolution, FPS, bitrate, and viewer count).

---

## 🚀 Getting Started

### For Broadcasters (Host)
1. Download the latest **`Instalar_StreamPlux.exe`** from the [Releases](../../releases) page and run the installer.
2. Launch **StreamPlux** from your Desktop.
3. Select your capture source (**Full Screen** or **Single Window**), resolution, FPS, and audio preferences.
4. Click **▶ INICIAR TRANSMISSÃO** (*Start Stream*).
5. Click **🌐 Gerar Link Público (Internet)** to create a shareable link, copy it, and send it to your viewers.

### For Viewers
1. Open the invite link in any modern web browser (Chrome, Edge, Firefox, Safari, or mobile).
2. Watch in real time and use the built-in live chat—**no downloads or sign-ups needed**.

---

## 🛠️ Running or Building from Source

If you want to run **StreamPlux** directly from the Python source code:

```bash
# 1. Clone the repository
git clone https://github.com/YOUR-USERNAME/StreamPlux.git
cd StreamPlux

# 2. Run the app (includes a 1-click dependency installer on first launch)
python streamplux_app.py
```

### Packaging into a Standalone `.EXE`
```bash
pip install pyinstaller
pyinstaller --noconfirm --onedir --windowed --name "StreamPlux" --icon="streamplux.ico" --collect-all aiortc --collect-all av --collect-all soundcard --hidden-import=PIL._tkinter_finder streamplux_app.py
```

---

## 🧰 Tech Stack
- **UI:** Python + Native `Tkinter` (Custom Dark Theme)
- **WebRTC & Signaling:** `aiortc` + `aiohttp` (Embedded HTTP/WebSocket Server)
- **Video & Audio Pipeline:** `mss`, `PyAV` (`libswscale`), `NumPy`, `SoundCard` (WASAPI Loopback)
- **Public Tunneling:** Cloudflare Quick Tunnels (`cloudflared`)
