#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
StreamPlux Desktop (Edição Ultra Leve / Eco-CPU)
================================================================================
Aplicativo nativo de PC (sem depender de Chrome/Edge/Electron) capaz de:
  • Capturar Tela Inteira (múltiplos monitores) ou Janela Única (jogos/apps)
  • Transmitir em 1080p (Full HD), 720p, 480p ou Nativa a 60 FPS, 30 FPS ou 15 FPS
  • Conversão e redimensionamento direto em C (libswscale SIMD) para baixo uso de CPU
  • Economia automática em espera (8 FPS quando 0 espectadores estão conectados)
  • Compartilhar Áudio do Sistema/Jogo (WASAPI Loopback) + Microfone opcional
  • Gerar Link de Convite (Rede Local e Link Público HTTPS via Cloudflare Tunnel)
  • Permitir que espectadores assistam pelo navegador SEM BAIXAR NADA (WebRTC)
================================================================================
"""

import asyncio
import ctypes
import fractions
import json
import os
import platform
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import tkinter as tk
from tkinter import ttk

# ==============================================================================
# 1. VERIFICAÇÃO DE DEPENDÊNCIAS COM INSTALADOR VISUAL EM 1 CLIQUE
# ==============================================================================
REQUIRED_PACKAGES = [
    ("aiohttp", "aiohttp"),
    ("aiortc", "aiortc"),
    ("av", "av"),
    ("mss", "mss"),
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
]

OPTIONAL_AUDIO_PACKAGE = ("soundcard", "soundcard")


def check_missing_packages():
    """Retorna lista de pacotes pip que ainda não estão instalados."""
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    try:
        __import__(OPTIONAL_AUDIO_PACKAGE[0])
    except ImportError:
        missing.append(OPTIONAL_AUDIO_PACKAGE[1])
    return missing


def launch_dependency_installer(missing_pkgs):
    """Abre janela escura nativa para instalar dependências com 1 clique."""
    root = tk.Tk()
    root.title("StreamPlux — Configuração Inicial")
    root.geometry("560x380")
    root.configure(bg="#09090f")
    root.resizable(False, False)

    tk.Label(
        root,
        text="StreamPlux Desktop",
        font=("Segoe UI", 18, "bold"),
        fg="#a78bfa",
        bg="#09090f",
    ).pack(pady=(22, 4))

    tk.Label(
        root,
        text="Primeira execução: instale os módulos nativos de vídeo, áudio e WebRTC.",
        font=("Segoe UI", 10),
        fg="#d1d5db",
        bg="#09090f",
    ).pack(pady=(0, 14))

    log_box = tk.Text(
        root,
        height=10,
        width=64,
        bg="#11121c",
        fg="#e5e7eb",
        font=("Consolas", 9),
        relief="flat",
        padx=10,
        pady=10,
    )
    log_box.pack(padx=20, pady=4)
    log_box.insert(
        "end",
        "Pacotes necessários:\n  " + ", ".join(missing_pkgs) + "\n\n"
        "Clique no botão abaixo para instalar automaticamente.\n",
    )
    log_box.configure(state="disabled")

    def append_log(text):
        def _do():
            log_box.configure(state="normal")
            log_box.insert("end", text)
            log_box.see("end")
            log_box.configure(state="disabled")

        root.after(0, _do)

    install_btn = tk.Button(
        root,
        text="Instalar Dependências e Abrir StreamPlux",
        font=("Segoe UI", 10, "bold"),
        bg="#7c3aed",
        fg="white",
        activebackground="#6d28d9",
        activeforeground="white",
        relief="flat",
        padx=18,
        pady=10,
        cursor="hand2",
    )
    install_btn.pack(pady=16)

    def run_install():
        install_btn.configure(state="disabled", text="Instalando... Aguarde")

        def worker():
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + missing_pkgs
            append_log(f"\n> Executando: {' '.join(cmd)}\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                for line in proc.stdout:
                    append_log(line)
                proc.wait()
                if proc.returncode == 0:
                    append_log("\n✔ Instalação concluída! Reiniciando o StreamPlux...\n")
                    time.sleep(1.0)
                    root.after(0, lambda: _restart_app(root))
                else:
                    append_log("\n✖ Erro na instalação. Tente instalar manualmente via terminal.\n")
                    root.after(
                        0,
                        lambda: install_btn.configure(
                            state="normal", text="Tentar Novamente"
                        ),
                    )
            except Exception as exc:
                append_log(f"\n✖ Falha ao executar pip: {exc}\n")
                root.after(
                    0,
                    lambda: install_btn.configure(
                        state="normal", text="Tentar Novamente"
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _restart_app(win):
        win.destroy()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    install_btn.configure(command=run_install)
    root.mainloop()
    sys.exit(0)


_missing = check_missing_packages()
_missing_required = [p for p in _missing if p != "soundcard"]
if _missing_required:
    launch_dependency_installer(_missing)

# Importação segura dos módulos instalados
import av
import mss
import numpy as np
from aiohttp import web, WSMsgType
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaRelay
from PIL import Image, ImageTk

try:
    from aiortc import RTCRtpSender

    HAS_RTP_SENDER = True
except ImportError:
    HAS_RTP_SENDER = False

try:
    import soundcard as sc

    HAS_SOUNDCARD = True
except Exception:
    HAS_SOUNDCARD = False

# ==============================================================================
# 2. AJUSTE DE DPI E ENUMERAÇÃO NATIVA DE JANELAS (WINDOWS / MULTIPLATAFORMA)
# ==============================================================================
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def list_open_windows(exclude_title="StreamPlux"):
    """Lista janelas visíveis no sistema para transmissão de janela única."""
    windows = []
    if not IS_WINDOWS:
        return windows

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or exclude_title.lower() in title.lower():
            return True
        if title in ("Program Manager", "Microsoft Text Input Application"):
            return True

        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w >= 160 and h >= 100:
                windows.append(
                    {
                        "hwnd": int(hwnd),
                        "title": title,
                        "rect": (rect.left, rect.top, w, h),
                    }
                )
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        pass
    return windows


def get_window_rect_by_hwnd(hwnd):
    """Obtém a posição atualizada de uma janela pelo HWND."""
    if not IS_WINDOWS or not hwnd:
        return None
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None

    rect = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 32 and h > 32:
            return {
                "left": int(rect.left),
                "top": int(rect.top),
                "width": int(w),
                "height": int(h),
            }
    return None


def get_local_ip():
    """Descobre o IP local da máquina na rede Wi-Fi/Cabo."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def find_free_port(preferred=8765):
    """Encontra uma porta TCP livre começando pela porta preferencial."""
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return 0


def generate_room_code():
    """Gera um código curto exclusivo para a sala de transmissão."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "PLUX-" + "".join(random.choice(chars) for _ in range(6))


# ==============================================================================
# 3. MOTOR DE CAPTURA ULTRA LEVE (LIBSWSCALE SIMD + ECONOMIA EM ESPERA)
# ==============================================================================
RESOLUTION_MAP = {
    "1080p (Full HD)": (1920, 1080),
    "720p (HD)": (1280, 720),
    "480p (SD Leve)": (854, 480),
    "Nativa": None,
}


class ScreenCapturer:
    """
    Thread dedicada de captura otimizada para baixo consumo de CPU:
      • Converte BGRA -> YUV420p e redimensiona diretamente em C via PyAV (libswscale SIMD)
      • Reduz automaticamente para 8 FPS enquanto 0 espectadores estão conectados
      • Gera miniatura de preview apenas quando a janela está visível e com preview ativo
    """

    def __init__(self):
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

        # Configurações dinâmicas
        self.capture_mode = "monitor"
        self.monitor_index = 1
        self.window_hwnd = None
        self.target_resolution = (1920, 1080)
        self.target_fps = 60
        self.eco_mode = False
        self.active_viewers = 0
        self.preview_active = True

        # Quadro pronto em YUV420p (C-backed av.VideoFrame) e miniatura leve de preview
        self.latest_yuv_frame = None
        self.latest_preview_rgb = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0
        self.is_standby_fps = False

    def configure(
        self,
        mode,
        monitor_index,
        window_hwnd,
        resolution_key,
        fps,
        eco_mode=False,
    ):
        with self.lock:
            self.capture_mode = mode
            self.monitor_index = monitor_index
            self.window_hwnd = window_hwnd
            self.target_resolution = RESOLUTION_MAP.get(resolution_key, (1920, 1080))
            self.target_fps = max(5, min(60, int(fps)))
            self.eco_mode = bool(eco_mode)

    def set_viewer_count(self, count: int):
        with self.lock:
            self.active_viewers = max(0, int(count))

    def set_preview_active(self, active: bool):
        with self.lock:
            self.preview_active = bool(active)
            if not active:
                self.latest_preview_rgb = None

    def get_effective_fps(self):
        with self.lock:
            if self.active_viewers == 0:
                return 8
            if self.eco_mode:
                return min(30, self.target_fps)
            return self.target_fps

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        with self.lock:
            self.latest_yuv_frame = None
            self.latest_preview_rgb = None
            self.actual_fps = 0.0
            self.actual_width = 0
            self.actual_height = 0

    def get_yuv_frame(self):
        with self.lock:
            return self.latest_yuv_frame

    def get_preview_rgb(self):
        with self.lock:
            return self.latest_preview_rgb

    def _capture_loop(self):
        fps_counter = 0
        fps_timer = time.perf_counter()
        last_preview_ts = 0.0

        with mss.mss() as sct:
            virtual_screen = sct.monitors[0]
            v_left = virtual_screen["left"]
            v_top = virtual_screen["top"]
            v_right = v_left + virtual_screen["width"]
            v_bottom = v_top + virtual_screen["height"]

            while self.running:
                loop_start = time.perf_counter()

                with self.lock:
                    mode = self.capture_mode
                    mon_idx = self.monitor_index
                    hwnd = self.window_hwnd
                    target_res = self.target_resolution
                    configured_fps = self.target_fps
                    eco = self.eco_mode
                    viewers = self.active_viewers
                    want_preview = self.preview_active

                # Economia automática de CPU: 8 FPS em espera; FPS total quando há espectadores
                if viewers == 0:
                    effective_fps = 8
                    standby = True
                else:
                    effective_fps = min(30, configured_fps) if eco else configured_fps
                    standby = False

                frame_interval = 1.0 / effective_fps

                region = None
                if mode == "window" and hwnd:
                    win_rect = get_window_rect_by_hwnd(hwnd)
                    if win_rect:
                        left = max(v_left, win_rect["left"])
                        top = max(v_top, win_rect["top"])
                        right = min(v_right, win_rect["left"] + win_rect["width"])
                        bottom = min(v_bottom, win_rect["top"] + win_rect["height"])
                        if right - left > 32 and bottom - top > 32:
                            region = {
                                "left": left,
                                "top": top,
                                "width": right - left,
                                "height": bottom - top,
                            }

                if region is None:
                    monitors = sct.monitors
                    safe_idx = mon_idx if 0 <= mon_idx < len(monitors) else 1
                    region = monitors[safe_idx]

                try:
                    raw = sct.grab(region)
                    src_w, src_h = raw.width, raw.height

                    # Visão zero-copy do buffer BGRA da captura
                    bgra = np.frombuffer(raw.bgra, dtype=np.uint8).reshape(
                        (src_h, src_w, 4)
                    )

                    # Calcula dimensões pares de saída
                    if target_res is not None:
                        max_w, max_h = target_res
                        if eco and max_w > 1280:
                            max_w, max_h = 1280, 720
                        scale = min(max_w / src_w, max_h / src_h)
                        if scale < 1.0:
                            new_w = max(2, int(src_w * scale) & ~1)
                            new_h = max(2, int(src_h * scale) & ~1)
                        else:
                            new_w, new_h = src_w & ~1, src_h & ~1
                    else:
                        new_w, new_h = src_w & ~1, src_h & ~1

                    # Conversão e redimensionamento direto em C (libswscale SIMD) -> YUV420p
                    av_bgra = av.VideoFrame.from_ndarray(bgra, format="bgra")
                    yuv_frame = av_bgra.reformat(
                        width=new_w, height=new_h, format="yuv420p"
                    )

                    # Gera miniatura leve de preview (~7 FPS) apenas se janela não estiver minimizada
                    preview_rgb = None
                    now_perf = time.perf_counter()
                    if want_preview and (now_perf - last_preview_ts >= 0.14):
                        step = max(1, src_w // 480)
                        preview_rgb = np.ascontiguousarray(
                            bgra[::step, ::step, 2::-1]
                        )
                        last_preview_ts = now_perf

                    with self.lock:
                        self.latest_yuv_frame = yuv_frame
                        if preview_rgb is not None:
                            self.latest_preview_rgb = preview_rgb
                        self.actual_width = new_w
                        self.actual_height = new_h
                        self.is_standby_fps = standby

                    fps_counter += 1
                    if now_perf - fps_timer >= 1.0:
                        with self.lock:
                            self.actual_fps = fps_counter / (now_perf - fps_timer)
                        fps_counter = 0
                        fps_timer = now_perf

                except Exception:
                    time.sleep(0.05)

                elapsed = time.perf_counter() - loop_start
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)


# ==============================================================================
# 4. MOTOR DE CAPTURA DE ÁUDIO DO SISTEMA (WASAPI LOOPBACK) + MICROFONE
# ==============================================================================
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_SAMPLES_PER_FRAME = 960  # 20ms exatos para WebRTC Opus


class AudioCapturer:
    """Captura áudio do sistema (WASAPI Loopback) e opcionalmente mixa o microfone."""

    def __init__(self):
        self.running = False
        self.sys_thread = None
        self.mic_thread = None
        self.audio_queue = queue.Queue(maxsize=8)
        self.mic_latest = np.zeros(
            (AUDIO_SAMPLES_PER_FRAME, AUDIO_CHANNELS), dtype=np.float32
        )
        self.mic_lock = threading.Lock()

        self.share_system_audio = True
        self.share_mic_audio = False
        self.selected_speaker_id = None
        self.level = 0.0
        self.status_text = "Pronto"

    @staticmethod
    def list_output_devices():
        if not HAS_SOUNDCARD:
            return []
        try:
            if IS_WINDOWS:
                try:
                    ctypes.windll.ole32.CoInitialize(None)
                except Exception:
                    pass
            speakers = sc.all_speakers()
            return [(sp.id, sp.name) for sp in speakers]
        except Exception:
            return []

    def configure(self, share_system, share_mic, speaker_id=None):
        self.share_system_audio = share_system
        self.share_mic_audio = share_mic
        self.selected_speaker_id = speaker_id

    def start(self):
        if self.running:
            return
        self.running = True
        self._clear_queue()

        if not HAS_SOUNDCARD or (
            not self.share_system_audio and not self.share_mic_audio
        ):
            self.status_text = (
                "Sem Áudio" if HAS_SOUNDCARD else "Módulo soundcard ausente"
            )
            self.level = 0.0
            return

        if self.share_mic_audio:
            self.mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
            self.mic_thread.start()

        self.sys_thread = threading.Thread(target=self._system_loop, daemon=True)
        self.sys_thread.start()

    def stop(self):
        self.running = False
        if self.sys_thread and self.sys_thread.is_alive():
            self.sys_thread.join(timeout=1.2)
        if self.mic_thread and self.mic_thread.is_alive():
            self.mic_thread.join(timeout=1.2)
        self._clear_queue()
        self.level = 0.0
        self.status_text = "Parado"

    def _clear_queue(self):
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _mic_loop(self):
        if IS_WINDOWS:
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass
        try:
            mic = sc.default_microphone()
            if mic is None:
                return
            with mic.recorder(
                samplerate=AUDIO_SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                blocksize=AUDIO_SAMPLES_PER_FRAME,
            ) as rec:
                while self.running and self.share_mic_audio:
                    data = rec.record(numframes=AUDIO_SAMPLES_PER_FRAME)
                    if data.ndim == 1:
                        data = np.column_stack([data, data])
                    elif data.shape[1] == 1:
                        data = np.repeat(data, 2, axis=1)
                    with self.mic_lock:
                        self.mic_latest = data[:, :2].astype(np.float32)
        except Exception:
            pass

    def _system_loop(self):
        if IS_WINDOWS:
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass

        loopback_mic = None
        if self.share_system_audio:
            try:
                target_speaker = None
                if self.selected_speaker_id:
                    for sp in sc.all_speakers():
                        if sp.id == self.selected_speaker_id:
                            target_speaker = sp
                            break
                if target_speaker is None:
                    target_speaker = sc.default_speaker()

                if target_speaker is not None:
                    loopback_mic = sc.get_microphone(
                        id=str(target_speaker.name), include_loopback=True
                    )
            except Exception:
                loopback_mic = None

        if loopback_mic is None and self.share_system_audio:
            self.status_text = "Loopback indisponível"
            if not self.share_mic_audio:
                return

        self.status_text = "Áudio Ativo"
        try:
            if loopback_mic is not None:
                with loopback_mic.recorder(
                    samplerate=AUDIO_SAMPLE_RATE,
                    channels=AUDIO_CHANNELS,
                    blocksize=AUDIO_SAMPLES_PER_FRAME,
                ) as rec:
                    while self.running:
                        sys_data = rec.record(numframes=AUDIO_SAMPLES_PER_FRAME)
                        if sys_data.ndim == 1:
                            sys_data = np.column_stack([sys_data, sys_data])
                        elif sys_data.shape[1] == 1:
                            sys_data = np.repeat(sys_data, 2, axis=1)
                        mixed = sys_data[:, :2].astype(np.float32)

                        if self.share_mic_audio:
                            with self.mic_lock:
                                mixed = np.clip(
                                    mixed + self.mic_latest * 0.9, -1.0, 1.0
                                )

                        self._push_audio_chunk(mixed)
            else:
                while self.running:
                    time.sleep(0.02)
                    with self.mic_lock:
                        mixed = self.mic_latest.copy()
                    self._push_audio_chunk(mixed)
        except Exception as exc:
            self.status_text = f"Aviso áudio: {str(exc)[:24]}"
            self.level = 0.0

    def _push_audio_chunk(self, float_stereo):
        rms = float(np.sqrt(np.mean(np.square(float_stereo))))
        self.level = min(1.0, rms * 4.5)

        pcm = (np.clip(float_stereo, -1.0, 1.0) * 32767.0).astype(np.int16)
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
        self.audio_queue.put_nowait(pcm)


# ==============================================================================
# 5. FAIXAS DE MÍDIA WEBRTC (AIORTC VIDEO YUV420P + AUDIO TRACKS)
# ==============================================================================
class ScreenVideoTrack(MediaStreamTrack):
    """Fornece quadros YUV420p já convertidos em C para o motor WebRTC (aiortc)."""

    kind = "video"

    def __init__(self, capturer: ScreenCapturer):
        super().__init__()
        self.capturer = capturer
        self._start = None
        self._last_pts = -1
        self._blank_frame = av.VideoFrame.from_ndarray(
            np.zeros((720, 1280, 3), dtype=np.uint8), format="bgr24"
        ).reformat(format="yuv420p")

    async def recv(self):
        fps = max(5, self.capturer.get_effective_fps())
        frame_duration = 1.0 / fps

        if self._start is None:
            self._start = time.perf_counter()
        else:
            now = time.perf_counter()
            next_time = max(
                now, self._start + ((self._last_pts / 90000.0) + frame_duration)
            )
            delay = next_time - now
            if delay > 0:
                await asyncio.sleep(delay)

        yuv_frame = self.capturer.get_yuv_frame()
        if yuv_frame is None:
            yuv_frame = self._blank_frame

        pts = int((time.perf_counter() - self._start) * 90000)
        if pts <= self._last_pts:
            pts = self._last_pts + int(90000 / fps)
        self._last_pts = pts

        yuv_frame.pts = pts
        yuv_frame.time_base = fractions.Fraction(1, 90000)
        return yuv_frame


class SystemAudioTrack(MediaStreamTrack):
    """Fornece pacotes PCM estéreo 48kHz de 20ms do AudioCapturer para o WebRTC."""

    kind = "audio"

    def __init__(self, audio_capturer: AudioCapturer):
        super().__init__()
        self.audio_capturer = audio_capturer
        self._start = None
        self._pts = 0
        self._silence = np.zeros(
            (1, AUDIO_SAMPLES_PER_FRAME * AUDIO_CHANNELS), dtype=np.int16
        )

    async def recv(self):
        if self._start is None:
            self._start = time.perf_counter()
        else:
            expected_time = self._start + (self._pts / AUDIO_SAMPLE_RATE)
            delay = expected_time - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)

        try:
            pcm = self.audio_capturer.audio_queue.get_nowait()
            packed = np.ascontiguousarray(pcm.reshape(1, -1))
        except queue.Empty:
            packed = self._silence

        audio_frame = av.AudioFrame.from_ndarray(
            packed, format="s16", layout="stereo"
        )
        audio_frame.sample_rate = AUDIO_SAMPLE_RATE
        audio_frame.pts = self._pts
        audio_frame.time_base = fractions.Fraction(1, AUDIO_SAMPLE_RATE)
        self._pts += AUDIO_SAMPLES_PER_FRAME
        return audio_frame


# ==============================================================================
# 6. PÁGINA WEB DO ESPECTADOR (SERVE DIRETO NO NAVEGADOR SEM BAIXAR NADA!)
# ==============================================================================
VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>StreamPlux — Assistir Transmissão ao Vivo</title>
  <style>
    :root {
      --bg: #09090f;
      --surface: #11121c;
      --card: #171926;
      --border: #262a40;
      --accent: #7c3aed;
      --cyan: #06b6d4;
      --live: #ef4444;
      --text: #f3f4f6;
      --muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Inter, system-ui, sans-serif; }
    body { background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }
    header {
      background: rgba(17, 18, 28, 0.92); backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--border); padding: 12px 20px;
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
    }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 19px; letter-spacing: -0.3px; }
    .brand-icon {
      width: 34px; height: 34px; border-radius: 10px;
      background: linear-gradient(135deg, #7c3aed, #06b6d4);
      display: flex; align-items: center; justify-content: center; font-size: 16px;
    }
    .badge {
      font-size: 11px; padding: 4px 10px; border-radius: 999px; font-weight: 700;
      background: rgba(124, 58, 237, 0.2); border: 1px solid rgba(124, 58, 237, 0.4); color: #c4b5fd;
    }
    .badge-live { background: rgba(239, 68, 68, 0.2); border-color: rgba(239, 68, 68, 0.5); color: #fca5a5; }
    .layout {
      flex: 1; max-width: 1400px; width: 100%; margin: 0 auto; padding: 18px;
      display: grid; grid-template-columns: 1fr 340px; gap: 18px;
    }
    @media (max-width: 980px) { .layout { grid-template-columns: 1fr; } }
    .player-column { display: flex; flex-direction: column; gap: 14px; }
    .video-shell {
      position: relative; background: #000; border-radius: 16px; overflow: hidden;
      border: 1px solid var(--border); aspect-ratio: 16 / 9;
      box-shadow: 0 0 40px -10px rgba(124, 58, 237, 0.28);
      display: flex; align-items: center; justify-content: center;
    }
    video { width: 100%; height: 100%; object-fit: contain; background: #000; }
    .overlay {
      position: absolute; inset: 0; background: rgba(9, 9, 15, 0.86);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      text-align: center; padding: 24px; gap: 12px; z-index: 5;
    }
    .hidden { display: none !important; }
    .btn {
      background: linear-gradient(135deg, #7c3aed, #06b6d4); color: #fff; border: none;
      padding: 12px 22px; border-radius: 12px; font-weight: 700; font-size: 14px; cursor: pointer;
    }
    .btn:hover { filter: brightness(1.1); }
    .controls-bar {
      position: absolute; bottom: 0; left: 0; right: 0; padding: 12px 16px;
      background: linear-gradient(to top, rgba(0,0,0,0.9), transparent);
      display: flex; align-items: center; justify-content: space-between; gap: 12px; z-index: 4;
    }
    .ctrl-group { display: flex; align-items: center; gap: 10px; }
    .icon-btn {
      background: rgba(255,255,255,0.12); color: #fff; border: none; border-radius: 8px;
      padding: 7px 11px; cursor: pointer; font-size: 13px; font-weight: 600;
    }
    .icon-btn:hover { background: rgba(255,255,255,0.22); }
    .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    @media (max-width: 600px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } }
    .stat-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 12px;
    }
    .stat-label { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
    .stat-value { font-size: 15px; font-weight: 700; font-family: Consolas, monospace; }
    .chat-box {
      background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
      display: flex; flex-direction: column; height: 560px; overflow: hidden;
    }
    .chat-header {
      padding: 14px 16px; border-bottom: 1px solid var(--border); background: var(--card);
      display: flex; justify-content: space-between; align-items: center; font-weight: 700; font-size: 14px;
    }
    .nick-bar {
      padding: 8px 14px; border-bottom: 1px solid var(--border); display: flex;
      align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--muted);
    }
    .nick-bar input {
      background: var(--card); border: 1px solid var(--border); color: #fff;
      border-radius: 8px; padding: 5px 8px; font-size: 12px; width: 150px; outline: none;
    }
    .chat-messages { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
    .msg { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; font-size: 13px; }
    .msg-head { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 3px; }
    .msg-host { color: #a78bfa; font-weight: 700; }
    .msg-viewer { color: #22d3ee; font-weight: 700; }
    .chat-form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); background: var(--card); }
    .chat-form input {
      flex: 1; background: var(--bg); border: 1px solid var(--border); color: #fff;
      border-radius: 10px; padding: 10px 12px; font-size: 13px; outline: none;
    }
    .toast {
      position: fixed; bottom: 18px; right: 18px; background: #171926; border: 1px solid #7c3aed;
      color: #fff; padding: 10px 16px; border-radius: 10px; font-size: 13px; z-index: 30;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-icon">📡</div>
      <span>StreamPlux</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
      <span id="statusBadge" class="badge">Conectando...</span>
      <span id="viewerBadge" class="badge">👥 1 online</span>
    </div>
  </header>

  <main class="layout">
    <section class="player-column">
      <div class="video-shell" id="videoShell">
        <video id="remoteVideo" autoplay playsinline></video>

        <div id="statusOverlay" class="overlay">
          <h2 id="overlayTitle">Conectando à transmissão...</h2>
          <p id="overlayDesc" style="color:var(--muted);font-size:13px;max-width:420px;">
            Estabelecendo túnel direto WebRTC com o aplicativo StreamPlux do host.
          </p>
          <button id="retryBtn" class="btn hidden" onclick="startWebRTC()">Reconectar</button>
        </div>

        <div id="unmuteOverlay" class="overlay hidden" style="background:rgba(0,0,0,0.68);">
          <button class="btn" onclick="enableAudio()">🔊 Clique para Ativar Som e Vídeo</button>
          <p style="color:#d1d5db;font-size:12px;">Seu navegador exige um clique para liberar o áudio da transmissão</p>
        </div>

        <div class="controls-bar">
          <div class="ctrl-group">
            <button class="icon-btn" id="muteBtn" onclick="toggleMute()">🔊 Som</button>
            <input id="volSlider" type="range" min="0" max="1" step="0.05" value="1" oninput="setVolume(this.value)" />
          </div>
          <div class="ctrl-group">
            <button class="icon-btn" onclick="togglePiP()">🗗 Flutuante</button>
            <button class="icon-btn" onclick="toggleFullscreen()">⛶ Tela Cheia</button>
          </div>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Resolução</div>
          <div id="stRes" class="stat-value">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Taxa de Quadros</div>
          <div id="stFps" class="stat-value">0 FPS</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Bitrate</div>
          <div id="stBitrate" class="stat-value">0 kbps</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Conexão</div>
          <div id="stState" class="stat-value">WebRTC</div>
        </div>
      </div>
    </section>

    <aside class="chat-box">
      <div class="chat-header">
        <span>💬 Chat ao Vivo</span>
        <span style="font-size:11px;color:#22d3ee;">Tempo Real</span>
      </div>
      <div class="nick-bar">
        <span>Seu apelido:</span>
        <input id="nickInput" type="text" maxlength="18" value="Espectador" />
      </div>
      <div id="chatMessages" class="chat-messages">
        <div class="msg" style="border-color:rgba(124,58,237,0.4);">
          Bem-vindo ao <strong>StreamPlux</strong>! Você está assistindo direto no navegador sem precisar instalar nada.
        </div>
      </div>
      <form class="chat-form" onsubmit="sendChat(event)">
        <input id="chatInput" type="text" placeholder="Digite sua mensagem..." autocomplete="off" />
        <button type="submit" class="btn" style="padding:10px 14px;">Enviar</button>
      </form>
    </aside>
  </main>

  <script>
    const params = new URLSearchParams(window.location.search);
    const roomCode = params.get("room") || "";
    const videoEl = document.getElementById("remoteVideo");
    const nickInput = document.getElementById("nickInput");
    nickInput.value = "Viewer-" + Math.floor(100 + Math.random() * 900);

    let pc = null;
    let ws = null;
    let statsTimer = null;
    let lastBytes = 0;
    let lastTs = 0;

    function showToast(text) {
      const el = document.createElement("div");
      el.className = "toast";
      el.textContent = text;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 3500);
    }

    function escapeHtml(str) {
      const d = document.createElement("div");
      d.textContent = str;
      return d.innerHTML;
    }

    function addChatMessage(sender, text, isHost) {
      const box = document.getElementById("chatMessages");
      const div = document.createElement("div");
      div.className = "msg";
      const cls = isHost ? "msg-host" : "msg-viewer";
      const tag = isHost ? " [HOST]" : "";
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      div.innerHTML = `<div class="msg-head"><span class="${cls}">${escapeHtml(sender)}${tag}</span><span style="color:#6b7280">${timeStr}</span></div><div>${escapeHtml(text)}</div>`;
      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
    }

    function connectWebSocket() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${location.host}/ws?room=${encodeURIComponent(roomCode)}`);
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "chat") {
            addChatMessage(data.sender, data.text, data.isHost);
          } else if (data.type === "viewers") {
            document.getElementById("viewerBadge").textContent = `👥 ${data.count} online`;
          } else if (data.type === "stream_status") {
            if (data.live) {
              startWebRTC();
            } else {
              document.getElementById("statusOverlay").classList.remove("hidden");
              document.getElementById("overlayTitle").textContent = "Transmissão Pausada / Offline";
              document.getElementById("overlayDesc").textContent = "Aguardando o streamer iniciar a transmissão no app StreamPlux...";
              document.getElementById("statusBadge").textContent = "Offline";
              document.getElementById("statusBadge").className = "badge";
            }
          }
        } catch (e) {}
      };
      ws.onclose = () => setTimeout(connectWebSocket, 3000);
    }

    function sendChat(e) {
      e.preventDefault();
      const input = document.getElementById("chatInput");
      const text = input.value.trim();
      if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({
        type: "chat",
        sender: nickInput.value.trim() || "Espectador",
        text
      }));
      input.value = "";
    }

    async function startWebRTC() {
      if (pc) {
        try { pc.close(); } catch (e) {}
      }
      clearInterval(statsTimer);

      document.getElementById("statusOverlay").classList.remove("hidden");
      document.getElementById("retryBtn").classList.add("hidden");
      document.getElementById("overlayTitle").textContent = "Conectando ao StreamPlux...";
      document.getElementById("overlayDesc").textContent = "Recebendo sinal de vídeo e áudio em tempo real.";

      pc = new RTCPeerConnection({
        iceServers: [
          { urls: "stun:stun.l.google.com:19302" },
          { urls: "stun:stun1.l.google.com:19302" }
        ]
      });

      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });

      const remoteStream = new MediaStream();
      videoEl.srcObject = remoteStream;

      pc.ontrack = (event) => {
        remoteStream.addTrack(event.track);
        document.getElementById("statusOverlay").classList.add("hidden");
        document.getElementById("statusBadge").textContent = "● AO VIVO";
        document.getElementById("statusBadge").className = "badge badge-live";

        videoEl.muted = false;
        const p = videoEl.play();
        if (p !== undefined) {
          p.then(() => {
            document.getElementById("unmuteOverlay").classList.add("hidden");
          }).catch(() => {
            videoEl.muted = true;
            videoEl.play();
            document.getElementById("unmuteOverlay").classList.remove("hidden");
          });
        }
      };

      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        document.getElementById("stState").textContent = st;
        if (st === "failed" || st === "disconnected") {
          document.getElementById("statusOverlay").classList.remove("hidden");
          document.getElementById("retryBtn").classList.remove("hidden");
          document.getElementById("overlayTitle").textContent = "Conexão Interrompida";
          document.getElementById("overlayDesc").textContent = "Clique em Reconectar para retomar a transmissão.";
        }
      };

      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        await new Promise((resolve) => {
          if (pc.iceGatheringState === "complete") return resolve();
          const timeout = setTimeout(resolve, 2000);
          pc.onicegatheringstatechange = () => {
            if (pc.iceGatheringState === "complete") {
              clearTimeout(timeout);
              resolve();
            }
          };
        });

        const resp = await fetch("/offer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type,
            room: roomCode
          })
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          document.getElementById("overlayTitle").textContent = errData.error || "Transmissão ainda não iniciada";
          document.getElementById("overlayDesc").textContent = "Assim que o streamer clicar em Iniciar Transmissão no app, o vídeo aparecerá aqui automaticamente.";
          document.getElementById("retryBtn").classList.remove("hidden");
          return;
        }

        const answer = await resp.json();
        await pc.setRemoteDescription(new RTCSessionDescription(answer));
        startStatsLoop();
      } catch (err) {
        document.getElementById("overlayTitle").textContent = "Não foi possível conectar";
        document.getElementById("overlayDesc").textContent = "Verifique se o aplicativo StreamPlux está aberto e transmitindo.";
        document.getElementById("retryBtn").classList.remove("hidden");
      }
    }

    function startStatsLoop() {
      lastBytes = 0;
      lastTs = 0;
      statsTimer = setInterval(async () => {
        if (!pc) return;
        const w = videoEl.videoWidth || 0;
        const h = videoEl.videoHeight || 0;
        document.getElementById("stRes").textContent = w ? `${w}×${h}` : "--";

        try {
          const stats = await pc.getStats();
          stats.forEach((r) => {
            if (r.type === "inbound-rtp" && r.kind === "video") {
              document.getElementById("stFps").textContent = `${Math.round(r.framesPerSecond || 0)} FPS`;
              if (lastBytes > 0 && r.timestamp > lastTs) {
                const kbps = Math.round(((r.bytesReceived - lastBytes) * 8) / ((r.timestamp - lastTs) / 1000) / 1000);
                document.getElementById("stBitrate").textContent = `${kbps} kbps`;
              }
              lastBytes = r.bytesReceived;
              lastTs = r.timestamp;
            }
          });
        } catch (e) {}
      }, 1000);
    }

    function enableAudio() {
      videoEl.muted = false;
      videoEl.volume = parseFloat(document.getElementById("volSlider").value) || 1;
      videoEl.play();
      document.getElementById("unmuteOverlay").classList.add("hidden");
      document.getElementById("muteBtn").textContent = "🔊 Som";
    }

    function toggleMute() {
      videoEl.muted = !videoEl.muted;
      document.getElementById("muteBtn").textContent = videoEl.muted ? "🔇 Mudo" : "🔊 Som";
    }

    function setVolume(v) {
      videoEl.volume = parseFloat(v);
      videoEl.muted = videoEl.volume === 0;
      document.getElementById("muteBtn").textContent = videoEl.muted ? "🔇 Mudo" : "🔊 Som";
    }

    function toggleFullscreen() {
      const el = document.getElementById("videoShell");
      if (!document.fullscreenElement) el.requestFullscreen().catch(() => {});
      else document.exitFullscreen().catch(() => {});
    }

    async function togglePiP() {
      try {
        if (document.pictureInPictureElement) await document.exitPictureInPicture();
        else await videoEl.requestPictureInPicture();
      } catch (e) {
        showToast("Modo flutuante indisponível neste navegador.");
      }
    }

    connectWebSocket();
    startWebRTC();
  </script>
</body>
</html>
"""


# ==============================================================================
# 7. SERVIDOR EMBUTIDO WEBRTC + CHAT + TÚNEL PÚBLICO HTTPS (CLOUDFLARED)
# ==============================================================================
class StreamServerEngine:
    """Servidor WebRTC + HTTP + WebSocket embutido com prioridade H.264."""

    def __init__(
        self,
        room_code,
        capturer: ScreenCapturer,
        audio_capturer: AudioCapturer,
        on_chat_cb,
        on_viewers_cb,
    ):
        self.room_code = room_code
        self.capturer = capturer
        self.audio_capturer = audio_capturer
        self.on_chat_cb = on_chat_cb
        self.on_viewers_cb = on_viewers_cb

        self.port = find_free_port(8765)
        self.loop = None
        self.thread = None
        self.runner = None
        self.is_live = False

        self.relay = MediaRelay()
        self.base_video_track = None
        self.base_audio_track = None
        self.pcs = set()
        self.websockets = set()

        self.tunnel_proc = None
        self.public_url = None

    def start_server(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_aiohttp())
        self.loop.run_forever()

    async def _start_aiohttp(self):
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_post("/offer", self._handle_offer)
        app.router.add_get("/ws", self._handle_ws)

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()

    async def _handle_index(self, request):
        return web.Response(text=VIEWER_HTML, content_type="text/html")

    async def _handle_offer(self, request):
        try:
            params = await request.json()
        except Exception:
            return web.json_response({"error": "Requisição inválida"}, status=400)

        if params.get("room") and params.get("room") != self.room_code:
            return web.json_response(
                {"error": "Código de sala inválido ou expirado."}, status=403
            )

        if not self.is_live or self.base_video_track is None:
            return web.json_response(
                {"error": "Aguardando o streamer iniciar a transmissão..."},
                status=503,
            )

        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        rtc_config = RTCConfiguration(
            iceServers=[
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            ]
        )
        pc = RTCPeerConnection(configuration=rtc_config)
        self.pcs.add(pc)
        self._notify_viewers()

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                self.pcs.discard(pc)
                self._notify_viewers()

        video_sender = pc.addTrack(self.relay.subscribe(self.base_video_track))
        if self.base_audio_track is not None:
            pc.addTrack(self.relay.subscribe(self.base_audio_track))

        # Prioriza codec H.264 (menor uso de CPU que VP8 em 1080p/720p)
        if HAS_RTP_SENDER:
            try:
                caps = RTCRtpSender.getCapabilities("video")
                h264_codecs = [
                    c for c in caps.codecs if c.mimeType.lower() == "video/h264"
                ]
                other_codecs = [
                    c for c in caps.codecs if c.mimeType.lower() != "video/h264"
                ]
                if h264_codecs:
                    for transceiver in pc.getTransceivers():
                        if transceiver.sender == video_sender:
                            transceiver.setCodecPreferences(
                                h264_codecs + other_codecs
                            )
            except Exception:
                pass

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.json_response(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        )

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        self.websockets.add(ws)
        self._notify_viewers()

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "chat":
                        sender = str(data.get("sender", "Espectador"))[:20]
                        text = str(data.get("text", "")).strip()[:300]
                        if text:
                            payload = {
                                "type": "chat",
                                "sender": sender,
                                "text": text,
                                "isHost": False,
                            }
                            await self._broadcast_ws(payload)
                            self.on_chat_cb(sender, text, False)
        except Exception:
            pass
        finally:
            self.websockets.discard(ws)
            self._notify_viewers()
        return ws

    async def _broadcast_ws(self, payload):
        dead = []
        raw = json.dumps(payload)
        for ws in list(self.websockets):
            try:
                await ws.send_str(raw)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.websockets.discard(d)

    def _notify_viewers(self):
        count = max(len(self.pcs), len(self.websockets))
        # Informa o capturador para alternar entre modo espera (8 FPS) e transmissão ativa
        self.capturer.set_viewer_count(count)
        self.on_viewers_cb(count)
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._broadcast_ws({"type": "viewers", "count": count}), self.loop
            )

    def set_live(self, live: bool):
        if not self.loop:
            return

        async def _apply():
            self.is_live = live
            if live:
                self.relay = MediaRelay()
                self.base_video_track = ScreenVideoTrack(self.capturer)
                self.base_audio_track = SystemAudioTrack(self.audio_capturer)
            else:
                coros = [pc.close() for pc in list(self.pcs)]
                if coros:
                    await asyncio.gather(*coros, return_exceptions=True)
                self.pcs.clear()
                self.base_video_track = None
                self.base_audio_track = None
                self._notify_viewers()
            await self._broadcast_ws({"type": "stream_status", "live": live})

        asyncio.run_coroutine_threadsafe(_apply(), self.loop)

    def send_host_chat(self, sender, text):
        if not self.loop:
            return
        payload = {"type": "chat", "sender": sender, "text": text, "isHost": True}
        asyncio.run_coroutine_threadsafe(self._broadcast_ws(payload), self.loop)

    def start_public_tunnel(self, callback):
        def worker():
            try:
                exe = shutil.which("cloudflared")
                if not exe and IS_WINDOWS:
                    app_dir = os.path.join(
                        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "StreamPlux",
                    )
                    os.makedirs(app_dir, exist_ok=True)
                    exe_path = os.path.join(app_dir, "cloudflared.exe")
                    if not os.path.exists(exe_path):
                        callback(
                            "status",
                            "Baixando módulo de túnel seguro (cloudflared)...",
                        )
                        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
                        urllib.request.urlretrieve(url, exe_path)
                    exe = exe_path

                if not exe:
                    callback(
                        "error",
                        "Instale 'cloudflared' no sistema para gerar link público.",
                    )
                    return

                callback("status", "Gerando link público HTTPS...")
                self.stop_public_tunnel()

                self.tunnel_proc = subprocess.Popen(
                    [
                        exe,
                        "tunnel",
                        "--url",
                        f"http://127.0.0.1:{self.port}",
                        "--no-autoupdate",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
                )

                url_pattern = re.compile(
                    r"https://[a-zA-Z0-9-]+\.trycloudflare\.com"
                )
                start_ts = time.time()
                for line in self.tunnel_proc.stdout:
                    match = url_pattern.search(line)
                    if match:
                        self.public_url = f"{match.group(0)}/?room={self.room_code}"
                        callback("ready", self.public_url)
                        return
                    if time.time() - start_ts > 25:
                        break
                callback(
                    "error",
                    "Tempo esgotado ao gerar link público. Tente novamente.",
                )
            except Exception as exc:
                callback("error", f"Falha no túnel público: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def stop_public_tunnel(self):
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
            except Exception:
                pass
            self.tunnel_proc = None

    def shutdown(self):
        self.stop_public_tunnel()
        if self.loop and self.loop.is_running():
            async def _close():
                for pc in list(self.pcs):
                    await pc.close()
                for ws in list(self.websockets):
                    await ws.close()
                if self.runner:
                    await self.runner.cleanup()
                self.loop.stop()

            asyncio.run_coroutine_threadsafe(_close(), self.loop)


# ==============================================================================
# 8. INTERFACE GRÁFICA NATIVA DO APLICATIVO STREAMPLUX (TKINTER DARK PRO)
# ==============================================================================
class StreamPluxApp:
    """Aplicativo Desktop Nativo StreamPlux — Leve, Escuro e Independente."""

    BG = "#09090f"
    SURFACE = "#11121c"
    CARD = "#171926"
    BORDER = "#25283d"
    ACCENT = "#7c3aed"
    ACCENT_HOVER = "#6d28d9"
    CYAN = "#06b6d4"
    LIVE = "#ef4444"
    SUCCESS = "#10b981"
    TEXT = "#f3f4f6"
    MUTED = "#9ca3af"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("StreamPlux — App Nativo Ultra Leve")
        self.root.geometry("1100x730")
        self.root.minsize(960, 650)
        self.root.configure(bg=self.BG)

        self.room_code = generate_room_code()
        self.capturer = ScreenCapturer()
        self.audio_capturer = AudioCapturer()
        self.server = StreamServerEngine(
            room_code=self.room_code,
            capturer=self.capturer,
            audio_capturer=self.audio_capturer,
            on_chat_cb=self._on_remote_chat,
            on_viewers_cb=self._on_viewers_update,
        )

        self.is_streaming = False
        self.stream_start_ts = None
        self.preview_enabled = tk.BooleanVar(value=True)
        self.capture_mode_var = tk.StringVar(value="monitor")
        self.resolution_var = tk.StringVar(value="1080p (Full HD)")
        self.fps_var = tk.StringVar(value="60")
        self.eco_cpu_var = tk.BooleanVar(value=False)
        self.system_audio_var = tk.BooleanVar(value=True)
        self.mic_audio_var = tk.BooleanVar(value=False)

        self.monitors_list = []
        self.windows_list = []
        self.audio_devices = []
        self._preview_photo = None

        self._configure_ttk_style()
        self._build_ui()
        self._refresh_sources()
        self._refresh_audio_devices()

        self.server.start_server()
        local_url = (
            f"http://{get_local_ip()}:{self.server.port}/?room={self.room_code}"
        )
        self.invite_url_var.set(local_url)
        self._set_toast(
            "Modo Eco-CPU ativo! Enquanto 0 pessoas assistem, o app reduz o FPS para economizar processador."
        )

        self._ui_tick()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_ttk_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=self.BG,
            background=self.CARD,
            foreground=self.TEXT,
            arrowcolor=self.CYAN,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self.BG)],
            selectbackground=[("readonly", self.ACCENT)],
            selectforeground=[("readonly", "#ffffff")],
        )

    def _build_ui(self):
        # --- BARRA SUPERIOR ---
        header = tk.Frame(
            self.root,
            bg=self.SURFACE,
            height=62,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left_hdr = tk.Frame(header, bg=self.SURFACE)
        left_hdr.pack(side="left", padx=18)

        tk.Label(
            left_hdr,
            text="📡 StreamPlux",
            font=("Segoe UI", 16, "bold"),
            fg="#ffffff",
            bg=self.SURFACE,
        ).pack(side="left")

        tk.Label(
            left_hdr,
            text=" ECO-CPU • ULTRA LEVE ",
            font=("Consolas", 8, "bold"),
            fg="#6ee7b7",
            bg="#064e3b",
            padx=6,
            pady=3,
        ).pack(side="left", padx=12)

        right_hdr = tk.Frame(header, bg=self.SURFACE)
        right_hdr.pack(side="right", padx=18)

        self.live_badge = tk.Label(
            right_hdr,
            text=" ● OFFLINE ",
            font=("Consolas", 9, "bold"),
            fg=self.MUTED,
            bg=self.CARD,
            padx=10,
            pady=4,
        )
        self.live_badge.pack(side="left", padx=6)

        self.timer_label = tk.Label(
            right_hdr,
            text="00:00:00",
            font=("Consolas", 10, "bold"),
            fg=self.TEXT,
            bg=self.SURFACE,
        )
        self.timer_label.pack(side="left", padx=8)

        self.viewers_badge = tk.Label(
            right_hdr,
            text=" 👥 0 assistindo ",
            font=("Segoe UI", 9, "bold"),
            fg=self.CYAN,
            bg=self.CARD,
            padx=10,
            pady=4,
        )
        self.viewers_badge.pack(side="left", padx=6)

        # --- CORPO PRINCIPAL (2 COLUNAS) ---
        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        body.columnconfigure(0, weight=5, minsize=430)
        body.columnconfigure(1, weight=7, minsize=480)
        body.rowconfigure(0, weight=1)

        left_col = tk.Frame(body, bg=self.BG)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        # CARD 1: Link de Convite
        card_link = self._make_card(
            left_col, "🔗 Link de Convite para Espectadores (Navegador)"
        )
        self.invite_url_var = tk.StringVar(value="Inicializando servidor...")
        url_entry = tk.Entry(
            card_link,
            textvariable=self.invite_url_var,
            font=("Consolas", 9),
            bg=self.BG,
            fg=self.CYAN,
            insertbackground="#fff",
            relief="flat",
            readonlybackground=self.BG,
            state="readonly",
        )
        url_entry.pack(fill="x", ipady=7, pady=(2, 8))

        link_btns = tk.Frame(card_link, bg=self.SURFACE)
        link_btns.pack(fill="x")

        tk.Button(
            link_btns,
            text="📋 Copiar Link",
            command=self._copy_invite_link,
            font=("Segoe UI", 9, "bold"),
            bg=self.CARD,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground="#fff",
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))

        self.public_btn = tk.Button(
            link_btns,
            text="🌐 Gerar Link Público (Internet)",
            command=self._generate_public_link,
            font=("Segoe UI", 9, "bold"),
            bg="#0e7490",
            fg="#ffffff",
            activebackground="#0891b2",
            activeforeground="#fff",
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.public_btn.pack(side="left", fill="x", expand=True)

        # CARD 2: Fonte de Captura
        card_src = self._make_card(
            left_col, "🖥️ Fonte de Captura (Tela Inteira ou Jogo)"
        )

        mode_row = tk.Frame(card_src, bg=self.SURFACE)
        mode_row.pack(fill="x", pady=(0, 8))

        tk.Radiobutton(
            mode_row,
            text="Tela Inteira (Monitor)",
            variable=self.capture_mode_var,
            value="monitor",
            command=self._on_mode_change,
            bg=self.SURFACE,
            fg=self.TEXT,
            selectcolor=self.BG,
            activebackground=self.SURFACE,
            activeforeground=self.CYAN,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 14))

        tk.Radiobutton(
            mode_row,
            text="Janela Única (Jogo / App)",
            variable=self.capture_mode_var,
            value="window",
            command=self._on_mode_change,
            bg=self.SURFACE,
            fg=self.TEXT,
            selectcolor=self.BG,
            activebackground=self.SURFACE,
            activeforeground=self.CYAN,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        src_pick_row = tk.Frame(card_src, bg=self.SURFACE)
        src_pick_row.pack(fill="x")

        self.source_combo = ttk.Combobox(
            src_pick_row,
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        self.source_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.source_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._apply_live_config()
        )

        tk.Button(
            src_pick_row,
            text="↻",
            command=self._refresh_sources,
            font=("Segoe UI", 10, "bold"),
            bg=self.CARD,
            fg=self.CYAN,
            relief="flat",
            width=3,
            cursor="hand2",
        ).pack(side="right")

        # CARD 3: Resolução & FPS + Modo Eco-CPU
        card_vid = self._make_card(
            left_col, "⚙️ Qualidade de Vídeo & Desempenho"
        )
        vid_grid = tk.Frame(card_vid, bg=self.SURFACE)
        vid_grid.pack(fill="x")
        vid_grid.columnconfigure(0, weight=1)
        vid_grid.columnconfigure(1, weight=1)

        tk.Label(
            vid_grid,
            text="Resolução:",
            font=("Segoe UI", 9),
            fg=self.MUTED,
            bg=self.SURFACE,
        ).grid(row=0, column=0, sticky="w", pady=(0, 3))
        tk.Label(
            vid_grid,
            text="Taxa de Quadros (FPS):",
            font=("Segoe UI", 9),
            fg=self.MUTED,
            bg=self.SURFACE,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 3))

        res_cb = ttk.Combobox(
            vid_grid,
            textvariable=self.resolution_var,
            values=list(RESOLUTION_MAP.keys()),
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        res_cb.grid(row=1, column=0, sticky="ew")
        res_cb.bind(
            "<<ComboboxSelected>>", lambda _e: self._apply_live_config()
        )

        fps_cb = ttk.Combobox(
            vid_grid,
            textvariable=self.fps_var,
            values=["60", "30", "15"],
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        fps_cb.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        fps_cb.bind(
            "<<ComboboxSelected>>", lambda _e: self._apply_live_config()
        )

        tk.Checkbutton(
            card_vid,
            text="Modo Eco-CPU (limita a 720p 30fps p/ jogos pesados)",
            variable=self.eco_cpu_var,
            command=self._apply_live_config,
            bg=self.SURFACE,
            fg="#6ee7b7",
            selectcolor=self.BG,
            activebackground=self.SURFACE,
            activeforeground="#6ee7b7",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(8, 0))

        # CARD 4: Áudio Compartilhado + VU Meter
        card_aud = self._make_card(
            left_col, "🔊 Áudio Compartilhado (Sistema & Microfone)"
        )

        tk.Checkbutton(
            card_aud,
            text="Compartilhar Áudio do Sistema / Jogo (Loopback)",
            variable=self.system_audio_var,
            bg=self.SURFACE,
            fg=self.TEXT,
            selectcolor=self.BG,
            activebackground=self.SURFACE,
            activeforeground=self.TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        self.audio_combo = ttk.Combobox(
            card_aud, state="readonly", style="Dark.TCombobox", font=("Segoe UI", 9)
        )
        self.audio_combo.pack(fill="x", pady=(4, 6))

        tk.Checkbutton(
            card_aud,
            text="Incluir Microfone (mixar minha voz com o jogo)",
            variable=self.mic_audio_var,
            bg=self.SURFACE,
            fg=self.TEXT,
            selectcolor=self.BG,
            activebackground=self.SURFACE,
            activeforeground=self.TEXT,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        vu_row = tk.Frame(card_aud, bg=self.SURFACE)
        vu_row.pack(fill="x", pady=(8, 0))
        self.audio_status_lbl = tk.Label(
            vu_row,
            text="Áudio: Pronto",
            font=("Consolas", 8),
            fg=self.MUTED,
            bg=self.SURFACE,
        )
        self.audio_status_lbl.pack(side="left")

        self.vu_canvas = tk.Canvas(
            vu_row, height=10, bg=self.BG, highlightthickness=0
        )
        self.vu_canvas.pack(side="right", fill="x", expand=True, padx=(10, 0))
        self.vu_bar = self.vu_canvas.create_rectangle(
            0, 0, 0, 10, fill=self.SUCCESS, outline=""
        )

        # BOTÕES DE CONTROLE PRINCIPAIS
        action_frame = tk.Frame(left_col, bg=self.BG)
        action_frame.pack(fill="x", pady=(10, 0))

        self.start_btn = tk.Button(
            action_frame,
            text="▶  INICIAR TRANSMISSÃO",
            command=self._start_streaming,
            font=("Segoe UI", 11, "bold"),
            bg=self.ACCENT,
            fg="#ffffff",
            activebackground=self.ACCENT_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            pady=12,
            cursor="hand2",
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = tk.Button(
            action_frame,
            text="■ ENCERRAR",
            command=self._stop_streaming,
            font=("Segoe UI", 10, "bold"),
            bg="#3f1d2b",
            fg="#fca5a5",
            activebackground=self.LIVE,
            activeforeground="#ffffff",
            relief="flat",
            padx=16,
            pady=12,
            state="disabled",
            cursor="hand2",
        )
        self.stop_btn.pack(side="right")

        # ======================================================================
        # COLUNA DIREITA: PREVIEW LEVE + TELEMETRIA + CHAT AO VIVO
        # ======================================================================
        right_col = tk.Frame(body, bg=self.BG)
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.rowconfigure(0, weight=5)
        right_col.rowconfigure(2, weight=5)
        right_col.columnconfigure(0, weight=1)

        preview_card = tk.Frame(
            right_col,
            bg=self.SURFACE,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        preview_card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        pv_hdr = tk.Frame(preview_card, bg=self.CARD, padx=12, pady=6)
        pv_hdr.pack(fill="x")
        tk.Label(
            pv_hdr,
            text="🎬 Pré-visualização Local",
            font=("Segoe UI", 9, "bold"),
            fg=self.TEXT,
            bg=self.CARD,
        ).pack(side="left")

        tk.Checkbutton(
            pv_hdr,
            text="Mostrar Preview (pausa sozinho ao minimizar)",
            variable=self.preview_enabled,
            bg=self.CARD,
            fg=self.MUTED,
            selectcolor=self.BG,
            activebackground=self.CARD,
            font=("Segoe UI", 8),
        ).pack(side="right")

        self.preview_label = tk.Label(
            preview_card,
            text="Clique em INICIAR TRANSMISSÃO\npara capturar tela ou jogo sem navegador.",
            font=("Segoe UI", 10),
            fg=self.MUTED,
            bg="#000000",
        )
        self.preview_label.pack(fill="both", expand=True, padx=6, pady=6)

        stats_bar = tk.Frame(right_col, bg=self.BG)
        stats_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for i in range(3):
            stats_bar.columnconfigure(i, weight=1)

        self.stat_res_lbl = self._make_stat_box(
            stats_bar, 0, "Resolução Real", "0 × 0"
        )
        self.stat_fps_lbl = self._make_stat_box(
            stats_bar, 1, "FPS de Captura", "0 FPS"
        )
        self.stat_net_lbl = self._make_stat_box(
            stats_bar, 2, "Motor WebRTC", f"H.264 :{self.server.port}"
        )

        chat_card = tk.Frame(
            right_col,
            bg=self.SURFACE,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        chat_card.grid(row=2, column=0, sticky="nsew")

        chat_hdr = tk.Frame(chat_card, bg=self.CARD, padx=12, pady=7)
        chat_hdr.pack(fill="x")
        tk.Label(
            chat_hdr,
            text="💬 Chat com os Espectadores",
            font=("Segoe UI", 9, "bold"),
            fg=self.TEXT,
            bg=self.CARD,
        ).pack(side="left")
        tk.Label(
            chat_hdr,
            text=f"Sala: {self.room_code}",
            font=("Consolas", 8, "bold"),
            fg=self.CYAN,
            bg=self.CARD,
        ).pack(side="right")

        self.chat_text = tk.Text(
            chat_card,
            bg=self.SURFACE,
            fg=self.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            wrap="word",
            padx=10,
            pady=8,
            state="disabled",
            height=7,
        )
        self.chat_text.pack(fill="both", expand=True)
        self.chat_text.tag_config(
            "host", foreground="#a78bfa", font=("Segoe UI", 9, "bold")
        )
        self.chat_text.tag_config(
            "viewer", foreground="#22d3ee", font=("Segoe UI", 9, "bold")
        )
        self.chat_text.tag_config(
            "sys", foreground="#9ca3af", font=("Segoe UI", 8, "italic")
        )

        chat_input_row = tk.Frame(chat_card, bg=self.CARD, padx=8, pady=8)
        chat_input_row.pack(fill="x")

        self.chat_entry = tk.Entry(
            chat_input_row,
            bg=self.BG,
            fg=self.TEXT,
            insertbackground="#fff",
            relief="flat",
            font=("Segoe UI", 9),
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.chat_entry.bind("<Return>", lambda _e: self._send_host_chat())

        tk.Button(
            chat_input_row,
            text="Enviar",
            command=self._send_host_chat,
            font=("Segoe UI", 9, "bold"),
            bg=self.ACCENT,
            fg="#ffffff",
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2",
        ).pack(side="right")

        self.toast_label = tk.Label(
            self.root,
            text="StreamPlux Desktop (Ultra Leve) inicializado.",
            font=("Segoe UI", 9),
            fg="#c4b5fd",
            bg=self.SURFACE,
            anchor="w",
            padx=16,
            pady=6,
        )
        self.toast_label.pack(fill="x", side="bottom")

    def _make_card(self, parent, title):
        outer = tk.Frame(
            parent,
            bg=self.SURFACE,
            highlightbackground=self.BORDER,
            highlightthickness=1,
        )
        outer.pack(fill="x", pady=(0, 10))
        tk.Label(
            outer,
            text=title,
            font=("Segoe UI", 9, "bold"),
            fg=self.TEXT,
            bg=self.SURFACE,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 6))
        inner = tk.Frame(outer, bg=self.SURFACE)
        inner.pack(fill="x", padx=12, pady=(0, 12))
        return inner

    def _make_stat_box(self, parent, col, label, default_val):
        box = tk.Frame(
            parent,
            bg=self.SURFACE,
            highlightbackground=self.BORDER,
            highlightthickness=1,
            padx=10,
            pady=7,
        )
        box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
        tk.Label(
            box, text=label, font=("Segoe UI", 8), fg=self.MUTED, bg=self.SURFACE
        ).pack(anchor="w")
        val_lbl = tk.Label(
            box,
            text=default_val,
            font=("Consolas", 11, "bold"),
            fg=self.TEXT,
            bg=self.SURFACE,
        )
        val_lbl.pack(anchor="w")
        return val_lbl

    # ==========================================================================
    # CONTROLES DE FONTES, ÁUDIO E TRANSMISSÃO
    # ==========================================================================
    def _refresh_sources(self):
        with mss.mss() as sct:
            self.monitors_list = []
            for idx, mon in enumerate(sct.monitors):
                if idx == 0:
                    continue
                self.monitors_list.append(
                    (idx, f"Monitor {idx} ({mon['width']}×{mon['height']})")
                )
            if len(sct.monitors) > 2:
                all_m = sct.monitors[0]
                self.monitors_list.append(
                    (0, f"Todos os Monitores ({all_m['width']}×{all_m['height']})")
                )

        self.windows_list = list_open_windows(exclude_title="StreamPlux")
        self._on_mode_change()

    def _on_mode_change(self):
        if self.capture_mode_var.get() == "window":
            if self.windows_list:
                labels = [w["title"][:62] for w in self.windows_list]
                self.source_combo["values"] = labels
                self.source_combo.current(0)
            else:
                self.source_combo["values"] = [
                    "Nenhuma janela detectada (use Tela Inteira)"
                ]
                self.source_combo.current(0)
        else:
            labels = [label for _, label in self.monitors_list]
            self.source_combo["values"] = labels
            if labels:
                self.source_combo.current(0)
        self._apply_live_config()

    def _refresh_audio_devices(self):
        self.audio_devices = AudioCapturer.list_output_devices()
        if self.audio_devices:
            self.audio_combo["values"] = [name for _, name in self.audio_devices]
            self.audio_combo.current(0)
        else:
            self.audio_combo["values"] = ["Dispositivo padrão do sistema"]
            self.audio_combo.current(0)

    def _apply_live_config(self):
        mode = self.capture_mode_var.get()
        idx = self.source_combo.current()
        mon_index = 1
        hwnd = None

        if mode == "monitor" and 0 <= idx < len(self.monitors_list):
            mon_index = self.monitors_list[idx][0]
        elif mode == "window" and 0 <= idx < len(self.windows_list):
            hwnd = self.windows_list[idx]["hwnd"]

        self.capturer.configure(
            mode=mode,
            monitor_index=mon_index,
            window_hwnd=hwnd,
            resolution_key=self.resolution_var.get(),
            fps=int(self.fps_var.get()),
            eco_mode=self.eco_cpu_var.get(),
        )

    def _start_streaming(self):
        self._apply_live_config()

        speaker_id = None
        a_idx = self.audio_combo.current()
        if 0 <= a_idx < len(self.audio_devices):
            speaker_id = self.audio_devices[a_idx][0]

        self.audio_capturer.configure(
            share_system=self.system_audio_var.get(),
            share_mic=self.mic_audio_var.get(),
            speaker_id=speaker_id,
        )

        self.capturer.start()
        self.audio_capturer.start()
        self.server.set_live(True)

        self.is_streaming = True
        self.stream_start_ts = time.time()

        self.start_btn.configure(state="disabled", text="● TRANSMITINDO")
        self.stop_btn.configure(state="normal", bg=self.LIVE, fg="#ffffff")
        self.live_badge.configure(text=" ● AO VIVO ", fg="#ffffff", bg=self.LIVE)

        self._append_chat_line(
            "Sistema",
            "Transmissão iniciada! Envie o link de convite para os espectadores.",
            "sys",
        )
        self._set_toast(
            "Transmissão AO VIVO! Qualquer pessoa com o link pode assistir pelo navegador."
        )

    def _stop_streaming(self):
        self.is_streaming = False
        self.server.set_live(False)
        self.capturer.stop()
        self.audio_capturer.stop()

        self.start_btn.configure(state="normal", text="▶  INICIAR TRANSMISSÃO")
        self.stop_btn.configure(state="disabled", bg="#3f1d2b", fg="#fca5a5")
        self.live_badge.configure(text=" ● OFFLINE ", fg=self.MUTED, bg=self.CARD)
        self.timer_label.configure(text="00:00:00")
        self.preview_label.configure(
            image="",
            text="Transmissão encerrada.\nClique em INICIAR TRANSMISSÃO quando estiver pronto.",
        )
        self._append_chat_line("Sistema", "Transmissão encerrada.", "sys")
        self._set_toast("Transmissão encerrada.")

    def _copy_invite_link(self):
        url = self.invite_url_var.get()
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._set_toast("Link de convite copiado para a área de transferência!")

    def _generate_public_link(self):
        self.public_btn.configure(
            state="disabled", text="⏳ Gerando Link Público..."
        )

        def on_tunnel_event(kind, msg):
            def _ui():
                if kind == "status":
                    self._set_toast(msg)
                elif kind == "ready":
                    self.invite_url_var.set(msg)
                    self.public_btn.configure(
                        state="normal", text="✔ Link Público Ativo"
                    )
                    self._set_toast(
                        "Link Público HTTPS pronto! Clique em 'Copiar Link' e envie para seus amigos."
                    )
                    self._append_chat_line(
                        "Sistema", f"Link público criado: {msg}", "sys"
                    )
                elif kind == "error":
                    self.public_btn.configure(
                        state="normal", text="🌐 Tentar Link Público"
                    )
                    self._set_toast(msg)

            self.root.after(0, _ui)

        self.server.start_public_tunnel(on_tunnel_event)

    # ==========================================================================
    # CHAT E LOOP DE ATUALIZAÇÃO DA INTERFACE
    # ==========================================================================
    def _send_host_chat(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, "end")
        self._append_chat_line("Streamer (Você)", text, "host")
        self.server.send_host_chat("Streamer", text)

    def _on_remote_chat(self, sender, text, is_host):
        self.root.after(
            0,
            lambda: self._append_chat_line(
                sender, text, "host" if is_host else "viewer"
            ),
        )

    def _on_viewers_update(self, count):
        self.root.after(
            0,
            lambda: self.viewers_badge.configure(
                text=f" 👥 {count} assistindo "
            ),
        )

    def _append_chat_line(self, sender, text, tag):
        self.chat_text.configure(state="normal")
        t_str = time.strftime("%H:%M")
        self.chat_text.insert("end", f"[{t_str}] {sender}: ", tag)
        self.chat_text.insert("end", f"{text}\n")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def _set_toast(self, message):
        self.toast_label.configure(text=f"ℹ  {message}")

    def _ui_tick(self):
        if self.is_streaming and self.stream_start_ts:
            elapsed = int(time.time() - self.stream_start_ts)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.timer_label.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

            self.stat_res_lbl.configure(
                text=f"{self.capturer.actual_width} × {self.capturer.actual_height}"
            )
            fps_suffix = " (Espera)" if self.capturer.is_standby_fps else ""
            self.stat_fps_lbl.configure(
                text=f"{self.capturer.actual_fps:.0f} FPS{fps_suffix}"
            )

            self.audio_status_lbl.configure(
                text=f"Áudio: {self.audio_capturer.status_text}"
            )
            vu_w = max(10, self.vu_canvas.winfo_width())
            bar_w = int(vu_w * self.audio_capturer.level)
            self.vu_canvas.coords(self.vu_bar, 0, 0, bar_w, 10)

            # Pausa preview automaticamente se a janela estiver minimizada
            is_minimized = self.root.state() == "iconic"
            should_preview = self.preview_enabled.get() and not is_minimized
            self.capturer.set_preview_active(should_preview)

            if should_preview:
                rgb = self.capturer.get_preview_rgb()
                if rgb is not None:
                    try:
                        pv_w = max(320, self.preview_label.winfo_width() - 8)
                        pv_h = max(180, self.preview_label.winfo_height() - 8)
                        img = Image.fromarray(rgb)
                        img.thumbnail((pv_w, pv_h), Image.BILINEAR)
                        self._preview_photo = ImageTk.PhotoImage(img)
                        self.preview_label.configure(
                            image=self._preview_photo, text=""
                        )
                    except Exception:
                        pass
            elif not self.preview_enabled.get():
                self.preview_label.configure(
                    image="",
                    text="Preview pausado para economizar CPU/GPU.\nSua transmissão continua AO VIVO normalmente!",
                )
        else:
            self.vu_canvas.coords(self.vu_bar, 0, 0, 0, 10)

        self.root.after(140, self._ui_tick)

    def _on_close(self):
        self.capturer.stop()
        self.audio_capturer.stop()
        self.server.shutdown()
        self.root.destroy()


# ==============================================================================
# 9. PONTO DE ENTRADA
# ==============================================================================
if __name__ == "__main__":
    root_window = tk.Tk()
    app = StreamPluxApp(root_window)
    root_window.mainloop()