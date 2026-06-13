#!/usr/bin/env python3
"""
Phoenix PhaseCancel — Interface Gráfica
Inclui setup de credenciais MiniMax + geração de áudios + processamento de vídeos.
"""

import array as arr_mod
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests
import customtkinter as ctk
ctk.set_appearance_mode("dark")
from tkinterdnd2 import TkinterDnD, DND_FILES

# Oculta janela CMD em todos os subprocessos no Windows
NO_WINDOW = 0x08000000

# ── Versão ────────────────────────────────────────────────────────────────────
APP_VERSION = "1.7"
VERSION_URL = "https://raw.githubusercontent.com/vitorcs-editor/phoenix-phasecancel/main/version.json"

# ── Caminhos ─────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

USER_DATA    = Path(os.environ.get("APPDATA", Path.home())) / "PhoenixPhaseCancel"
USER_DATA.mkdir(parents=True, exist_ok=True)

WHITES_ROOT  = USER_DATA / "whites"
ENV_FILE     = USER_DATA / ".env"
CONFIG_FILE  = USER_DATA / "config.json"
SCRIPTS_ROOT = BUNDLE_DIR / "scripts"
FFMPEG_BIN   = BUNDLE_DIR / "ffmpeg.exe"

for n in ["ed", "diabetes", "emagrecimento", "neuropatia", "memoria"]:
    (WHITES_ROOT / n).mkdir(parents=True, exist_ok=True)

# ── Configurações ─────────────────────────────────────────────────────────────
WHITE_GAIN_DB  = -28.0
SAMPLE_RATE    = 44100
AUDIO_BITRATE  = "128k"
VIDEO_EXTS     = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}
VALID_NICHES   = ["ed", "diabetes", "emagrecimento", "neuropatia", "memoria"]
MINIMAX_HOST   = "https://api.minimax.io"
MINIMAX_MODEL  = "speech-02-hd"

COMPRESS_PRESETS = {
    "Facebook Ads":  {"target_mb": 100},
    "Google Ads":    {"target_mb": 150},
    "Balanceado":    {"crf": "28", "preset": "medium"},
    "Menor tamanho": {"crf": "35", "preset": "fast"},
}

# Encoders GPU por ordem de preferência
GPU_ENCODERS = [
    ("h264_nvenc",  "NVIDIA"),
    ("h264_amf",    "AMD"),
    ("h264_qsv",    "Intel"),
]

def detect_gpu_encoder():
    """Detecta o melhor encoder de GPU disponível. Retorna (encoder, nome) ou (None, None)."""
    ff = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
    try:
        # Lista todos os encoders disponíveis
        result = subprocess.run(
            [ff, "-encoders"], capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW)
        encoders_output = result.stdout + result.stderr
    except Exception:
        return None, None

    for encoder, name in GPU_ENCODERS:
        if encoder not in encoders_output:
            continue
        # Testa com resolução mínima que NVENC aceita (320x240)
        try:
            test = subprocess.run(
                [ff, "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                 "-t", "0.1", "-vcodec", encoder, "-pix_fmt", "yuv420p",
                 "-f", "null", "-"],
                capture_output=True, timeout=15,
                creationflags=NO_WINDOW)
            if test.returncode == 0:
                return encoder, name
        except Exception:
            continue
    return None, None

# Cache da detecção (detecta uma vez só)
_GPU_ENCODER_CACHE = None
_GPU_NAME_CACHE = None

def get_gpu_encoder():
    global _GPU_ENCODER_CACHE, _GPU_NAME_CACHE
    if _GPU_ENCODER_CACHE is None:
        _GPU_ENCODER_CACHE, _GPU_NAME_CACHE = detect_gpu_encoder()
    return _GPU_ENCODER_CACHE, _GPU_NAME_CACHE

NICHE_LABELS = {
    "ed":            "ED",
    "diabetes":      "Diabetes",
    "emagrecimento": "Emagrecimento",
    "neuropatia":    "Neuropatia",
    "memoria":       "Memoria",
}

VOICE_POOL = [
    "English_expressive_narrator",
    "English_Insightful_Speaker",
    "English_Trustworth_Man",
    "English_CalmWoman",
    "English_Gentle-voiced_man",
    "English_Wiselady",
    "English_CalmWoman",
    "English_Diligent_Man",
]

COLORS = {
    "bg":       "#0B1630",
    "panel":    "#111F45",
    "accent":   "#F07800",
    "success":  "#2ECC71",
    "warning":  "#F5A623",
    "error":    "#E74C3C",
    "text":     "#FFFFFF",
    "subtext":  "#7A8AAD",
    "btn":      "#F07800",
    "btn_hover":"#D96800",
    "border":   "#1A2D5A",
}


# ── Config (último nicho usado) ───────────────────────────────────────────────

def load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_config(data):
    try:
        cfg = load_config()
        cfg.update(data)
        CONFIG_FILE.write_text(json.dumps(cfg), encoding="utf-8")
    except Exception:
        pass


# ── Credenciais ───────────────────────────────────────────────────────────────

def load_credentials():
    if not ENV_FILE.exists():
        return None, None
    api_key = group_id = None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            api_key = line.split("=", 1)[1].strip()
        elif line.startswith("MINIMAX_GROUP_ID="):
            group_id = line.split("=", 1)[1].strip()
    if api_key and "cole_" not in api_key and group_id and "cole_" not in group_id:
        return api_key, group_id
    return None, None

def save_credentials(api_key, group_id):
    ENV_FILE.write_text(
        f"MINIMAX_API_KEY={api_key}\nMINIMAX_GROUP_ID={group_id}\n",
        encoding="utf-8")

def whites_ok(niche):
    return bool(list((WHITES_ROOT / niche).glob("*.wav")))


# ── MiniMax API ───────────────────────────────────────────────────────────────

MINIMAX_ERRORS = {
    1004: "API Key invalida. Verifique suas credenciais.",
    1008: "Saldo MiniMax zerado. Recarregue em minimax.io/platform.",
    2053: "Credito insuficiente. Recarregue em minimax.io/platform.",
}

def _minimax_post(endpoint, api_key, group_id, payload):
    url = f"{MINIMAX_HOST}{endpoint}?GroupId={group_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
    except requests.exceptions.SSLError:
        raise RuntimeError("Erro de SSL. Verifique sua conexao com a internet.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Sem conexao com a internet.")
    except requests.exceptions.Timeout:
        raise RuntimeError("Timeout. Tente novamente.")
    return r.json()

def test_credentials(api_key, group_id):
    """Testa credenciais com chamada minima. Retorna (ok, mensagem)."""
    try:
        body = _minimax_post("/v1/t2a_v2", api_key, group_id, {
            "model": MINIMAX_MODEL,
            "text": "test",
            "stream": False,
            "voice_setting": {"voice_id": "English_CalmWoman", "speed": 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"sample_rate": 44100, "bitrate": 128000, "format": "mp3", "channel": 1},
        })
        code = body.get("base_resp", {}).get("status_code", 0)
        if code == 0:
            return True, "Credenciais validas!"
        elif code == 2053:
            return True, "Credenciais validas! (Saldo insuficiente — recarregue antes de gerar)"
        else:
            msg = MINIMAX_ERRORS.get(code, f"Erro {code}: {body.get('base_resp',{}).get('status_msg','')}")
            return False, msg
    except RuntimeError as e:
        return False, str(e)

def get_balance(api_key, group_id):
    """Busca saldo da conta MiniMax."""
    try:
        url = f"{MINIMAX_HOST}/v1/account/balance?GroupId={group_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            body = r.json()
            balance = body.get("balance", {})
            total = balance.get("total_balance") or balance.get("balance")
            if total is not None:
                return f"Saldo: ${float(total):.2f}"
    except Exception:
        pass
    return None

def synthesize(text, voice_id, api_key, group_id):
    body = _minimax_post("/v1/t2a_v2", api_key, group_id, {
        "model": MINIMAX_MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 44100, "bitrate": 128000, "format": "mp3", "channel": 1},
    })
    code = body.get("base_resp", {}).get("status_code", 0)
    if code != 0:
        msg = MINIMAX_ERRORS.get(code, f"MiniMax erro {code}: {body.get('base_resp',{}).get('status_msg','')}")
        raise RuntimeError(msg)
    audio_hex = body.get("data", {}).get("audio")
    if not audio_hex:
        raise RuntimeError("Sem audio na resposta MiniMax.")
    return bytes.fromhex(audio_hex)

def save_log(msg):
    """Salva log em arquivo no USER_DATA."""
    try:
        log_file = USER_DATA / "processamentos.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def notify_windows(title, message):
    """Notificacao balao no Windows."""
    try:
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            f'$n.ShowBalloonTip(5000, "{title}", "{message}", '
            "[System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep -Seconds 6; $n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)
    except Exception:
        pass

def mp3_to_wav(mp3_bytes, out_wav):
    ffmpeg = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0", "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
        input=mp3_bytes, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=NO_WINDOW)


# ── Engine de áudio ───────────────────────────────────────────────────────────

def _clip(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)

def get_ffmpeg():
    if FFMPEG_BIN.exists():
        return str(FFMPEG_BIN)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("ffmpeg nao encontrado.")

def run_ffmpeg(args):
    subprocess.run([get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, creationflags=NO_WINDOW)

def extract_mono_pcm(video_path, out_wav):
    try:
        run_ffmpeg(["-i", str(video_path), "-vn", "-ac", "1",
                    "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_wav)])
    except subprocess.CalledProcessError:
        raise RuntimeError(f"Nao foi possivel extrair audio de '{video_path.name}'. "
                           "Verifique se o video tem trilha de audio.")

def load_wav_mono_i16(path):
    with wave.open(str(path), "rb") as w:
        ch  = w.getnchannels()
        raw = w.readframes(w.getnframes())
    samples = arr_mod.array('h', raw)
    if ch > 1:
        samples = arr_mod.array('h', samples[0::ch])
    return samples

def pick_white_loop(target_len, niche):
    """Monta overlay ciclando pelos arquivos disponíveis com fade suave nas emendas."""
    niche_dir  = WHITES_ROOT / niche
    candidates = sorted(niche_dir.glob("*.wav"))
    if not candidates:
        raise RuntimeError(f"Audios do nicho '{niche}' nao encontrados.")

    FADE = int(SAMPLE_RATE * 0.4)   # 0.4s de fade nas emendas

    # Começa por um arquivo aleatório e cicla pelos demais em ordem
    start = random.randint(0, len(candidates) - 1)
    ordered = candidates[start:] + candidates[:start]

    result = arr_mod.array('h')
    idx    = 0
    first_name = ordered[0].name

    while len(result) < target_len:
        seg = list(load_wav_mono_i16(ordered[idx % len(ordered)]))
        slen = len(seg)
        fade = min(FADE, slen // 4)

        # Fade-in no início do segmento
        for i in range(fade):
            seg[i] = _clip(int(seg[i] * i / fade))

        # Fade-out no final do segmento
        for i in range(fade):
            seg[slen - 1 - i] = _clip(int(seg[slen - 1 - i] * i / fade))

        result.extend(arr_mod.array('h', seg))
        idx += 1

    del result[target_len:]
    return result, first_name

def write_stereo_wav(path, L, R):
    interleaved = arr_mod.array('h', [_clip(v) for pair in zip(L, R) for v in pair])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE); w.writeframes(interleaved.tobytes())

def mux_audio(video_path, audio_wav, out_path):
    run_ffmpeg(["-i", str(video_path), "-i", str(audio_wav),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
                "-ar", str(SAMPLE_RATE), "-ac", "2", "-shortest",
                "-map_metadata", "-1",       # remove todos os metadados
                "-metadata", "title=",       # titulo vazio
                "-metadata", "comment=",     # comentario vazio
                "-metadata", "encoder=",     # encoder vazio
                str(out_path)])

def process_one(video_path, niche):
    stem    = video_path.stem
    parent  = video_path.parent
    ext     = video_path.suffix
    out_dir = parent / "processados"
    out_dir.mkdir(exist_ok=True)
    fad_out  = out_dir / f"{stem}_FaD{ext}"
    fadw_out = out_dir / f"{stem}_FaDW{ext}"
    # Pula se ja foi processado
    if fad_out.exists() and fadw_out.exists():
        return fad_out, fadw_out, True  # True = skipped
    with tempfile.TemporaryDirectory(prefix="phasecancel_") as tmp:
        tmp = Path(tmp)
        mono_wav = tmp / "src_mono.wav"
        fad_wav  = tmp / "fad.wav"
        fadw_wav = tmp / "fadw.wav"
        extract_mono_pcm(video_path, mono_wav)
        O = load_wav_mono_i16(mono_wav)
        R_fad = arr_mod.array('h', [_clip(-s) for s in O])
        write_stereo_wav(fad_wav, O, R_fad)
        white_i16, white_name = pick_white_loop(len(O), niche)
        gain = 10 ** (WHITE_GAIN_DB / 20.0)
        L_fadw = arr_mod.array('h', [_clip(int(o + w * gain)) for o, w in zip(O, white_i16)])
        R_fadw = arr_mod.array('h', [_clip(int(-o + w * gain)) for o, w in zip(O, white_i16)])
        write_stereo_wav(fadw_wav, L_fadw, R_fadw)
        mux_audio(video_path, fad_wav, fad_out)
        mux_audio(video_path, fadw_wav, fadw_out)
    return fad_out, fadw_out, False  # False = processado agora


# ── CTk constants ─────────────────────────────────────────────────────────────

_ORANGE       = "#E8700A"
_ORANGE_HOVER = "#C95E00"
_NAVY         = "#080F1E"
_PANEL        = "#0E1A36"
_PANEL2       = "#0A1428"
_BORDER       = "#162348"
_WHITE        = "#E8EDF5"
_SUBTEXT      = "#6B7FA3"
_SUCCESS      = "#27AE60"
_ERROR        = "#E74C3C"
_WARNING      = "#F5A623"

LABEL_TO_NICHE = {v: k for k, v in NICHE_LABELS.items()}

# ── App Principal ─────────────────────────────────────────────────────────────

class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title("Phoenix PhaseCancel")
        self.geometry("800x860")
        self.minsize(700, 780)
        self.configure(fg_color=_NAVY)
        self.resizable(True, True)

        cfg = load_config()
        self.videos        = []
        self.niche_var     = tk.StringVar(value=cfg.get("last_niche", "ed"))
        self.status_var    = tk.StringVar(value="Aguardando...")
        self.processando   = False
        self._progress_max = 1

        # Header
        tk.Frame(self, bg=_ORANGE, height=3).pack(fill="x")
        hf = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        hf.pack(fill="x", padx=24, pady=(14, 8))
        lbl_row = ctk.CTkFrame(hf, fg_color="transparent")
        lbl_row.pack(side="left", anchor="w")
        ctk.CTkLabel(lbl_row, text="🔥  Phoenix PhaseCancel",
                     font=ctk.CTkFont("Segoe UI", 20, "bold"),
                     text_color=_WHITE).pack(side="left")
        ctk.CTkLabel(lbl_row, text=f"  v{APP_VERSION}",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=_SUBTEXT).pack(side="left", pady=(4, 0))
        self.btn_update = ctk.CTkButton(
            hf, text="Verificar atualização",
            command=self._verificar_atualizacao,
            fg_color=_BORDER, hover_color=_PANEL, text_color=_SUBTEXT,
            font=ctk.CTkFont("Segoe UI", 11), corner_radius=20, height=30, width=170)
        self.btn_update.pack(side="right")

        # Tabs
        self.tabs = ctk.CTkTabview(
            self, fg_color=_PANEL,
            segmented_button_fg_color=_NAVY,
            segmented_button_selected_color=_ORANGE,
            segmented_button_selected_hover_color=_ORANGE_HOVER,
            segmented_button_unselected_color=_BORDER,
            segmented_button_unselected_hover_color="#1E3566",
            text_color=_WHITE, corner_radius=6, border_width=0)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.tabs.add("  Processar  ")
        self.tabs.add("  Comprimir  ")

        self._build_tab_processar(self.tabs.tab("  Processar  "))
        self._build_tab_comprimir(self.tabs.tab("  Comprimir  "))
        self._check_ffmpeg()

    # ── Aba Processar ────────────────────────────────────────────────────────

    def _build_tab_processar(self, frame):
        frame.configure(fg_color=_PANEL)
        frame.grid_rowconfigure(4, weight=1)   # linha do log expande
        frame.grid_columnconfigure(0, weight=1)

        # Videos card — row 0
        vc = ctk.CTkFrame(frame, fg_color=_PANEL2, corner_radius=6)
        vc.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 4))
        ctk.CTkLabel(vc, text="VÍDEOS",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16, pady=(8, 3))

        self.drop_zone = ctk.CTkLabel(
            vc, text="  📂  Arraste os vídeos aqui  ou  clique em Selecionar",
            font=ctk.CTkFont("Segoe UI", 11), text_color=_SUBTEXT,
            fg_color=_BORDER, corner_radius=8, height=52, cursor="hand2")
        self.drop_zone.pack(fill="x", padx=16, pady=(0, 8))
        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)

        br1 = ctk.CTkFrame(vc, fg_color="transparent")
        br1.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(br1, text="Selecionar Vídeos", command=self._selecionar_videos,
                      fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      corner_radius=8, height=34).pack(side="left")
        ctk.CTkButton(br1, text="Limpar", command=self._limpar_videos,
                      fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
                      font=ctk.CTkFont("Segoe UI", 11),
                      corner_radius=8, height=34).pack(side="left", padx=(8, 0))

        self.lbl_contagem = ctk.CTkLabel(frame, text="",
                                          font=ctk.CTkFont("Segoe UI", 11),
                                          text_color=_SUBTEXT)
        self.lbl_contagem.grid(row=1, column=0, sticky="w", padx=16, pady=(2, 0))

        # Nicho card — row 2
        nc = ctk.CTkFrame(frame, fg_color=_PANEL2, corner_radius=6)
        nc.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        ctk.CTkLabel(nc, text="NICHO",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16, pady=(8, 4))

        self.niche_seg = ctk.CTkSegmentedButton(
            nc, values=[NICHE_LABELS[n] for n in VALID_NICHES],
            command=self._on_niche_change,
            fg_color=_BORDER, selected_color=_ORANGE,
            selected_hover_color=_ORANGE_HOVER,
            unselected_color=_BORDER, unselected_hover_color="#1E3566",
            text_color=_WHITE,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            corner_radius=8, height=36)
        self.niche_seg.pack(fill="x", padx=16, pady=(0, 6))
        self.niche_seg.set(NICHE_LABELS[self.niche_var.get()])

        ctk.CTkButton(nc, text="⚙   Configurar MiniMax",
                      command=self._open_config_modal,
                      fg_color="transparent", hover_color=_BORDER,
                      text_color=_SUBTEXT, font=ctk.CTkFont("Segoe UI", 11),
                      anchor="e", height=26, corner_radius=6).pack(
                          anchor="e", padx=16, pady=(0, 8))

        # Progress + status — row 3
        pf = ctk.CTkFrame(frame, fg_color="transparent")
        pf.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 0))
        self.progress = ctk.CTkProgressBar(
            pf, fg_color=_BORDER, progress_color=_ORANGE,
            corner_radius=4, height=6)
        self.progress.pack(fill="x")
        self.progress.set(0)
        self.lbl_status = ctk.CTkLabel(
            pf, text="Aguardando...",
            font=ctk.CTkFont("Segoe UI", 11), text_color=_SUBTEXT)
        self.lbl_status.pack(anchor="w", pady=(3, 0))
        self.status_var.trace_add("write", lambda *_: self.after(
            0, lambda: self.lbl_status.configure(text=self.status_var.get())))

        # Log — row 4 (expande)
        self.log_text = ctk.CTkTextbox(
            frame, fg_color=_PANEL2, text_color="#CBD5E1",
            font=ctk.CTkFont("Consolas", 10), corner_radius=10,
            border_width=0, state="disabled", wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew", padx=16, pady=8)

        # Botões — row 5
        br = ctk.CTkFrame(frame, fg_color="transparent")
        br.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.btn_processar = ctk.CTkButton(
            br, text="PROCESSAR", command=self._iniciar_processamento,
            fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            corner_radius=10, height=46, state="disabled")
        self.btn_processar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_abrir = ctk.CTkButton(
            br, text="Abrir Processados", command=self._abrir_processados,
            fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
            font=ctk.CTkFont("Segoe UI", 11),
            corner_radius=10, height=46, state="disabled")
        self.btn_abrir.pack(side="left")

    # ── Modal Configuração MiniMax ───────────────────────────────────────────

    def _open_config_modal(self):
        if hasattr(self, "_config_modal") and self._config_modal and \
                self._config_modal.winfo_exists():
            self._config_modal.lift(); self._config_modal.focus_force(); return

        win = ctk.CTkToplevel(self)
        win.title("Configuração MiniMax — Hack de Áudio")
        win.configure(fg_color=_NAVY)
        win.resizable(False, False)
        win.geometry("520x740")
        win.grab_set()
        self._config_modal = win

        ctk.CTkLabel(win, text="Credenciais MiniMax",
                     font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=_WHITE).pack(anchor="w", padx=24, pady=(20, 2))
        ctk.CTkLabel(win,
                     text="Crie sua conta em minimax.io/platform e cole as credenciais abaixo.",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=_SUBTEXT, wraplength=460).pack(anchor="w", padx=24)

        cf = ctk.CTkFrame(win, fg_color=_PANEL, corner_radius=6)
        cf.pack(fill="x", padx=24, pady=14)

        ctk.CTkLabel(cf, text="API KEY",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16, pady=(12, 2))
        self.entry_apikey = ctk.CTkEntry(cf, fg_color=_BORDER, text_color=_WHITE,
                                          border_color=_BORDER, show="*",
                                          font=ctk.CTkFont("Segoe UI", 11),
                                          height=36, corner_radius=8)
        self.entry_apikey.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(cf, text="GROUP ID  (número longo 18-19 dígitos)",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16)
        self.entry_groupid = ctk.CTkEntry(cf, fg_color=_BORDER, text_color=_WHITE,
                                           border_color=_BORDER,
                                           font=ctk.CTkFont("Segoe UI", 11),
                                           height=36, corner_radius=8)
        self.entry_groupid.pack(fill="x", padx=16, pady=(2, 0))

        br_cred = ctk.CTkFrame(cf, fg_color="transparent")
        br_cred.pack(fill="x", padx=16, pady=(12, 14))
        ctk.CTkButton(br_cred, text="Salvar", command=self._salvar_credenciais,
                      fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      corner_radius=8, height=34).pack(side="left")
        ctk.CTkButton(br_cred, text="Testar Credenciais",
                      command=self._testar_credenciais,
                      fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
                      font=ctk.CTkFont("Segoe UI", 11),
                      corner_radius=8, height=34).pack(side="left", padx=(8, 0))

        sr = ctk.CTkFrame(win, fg_color="transparent")
        sr.pack(fill="x", padx=24)
        self.lbl_cred_status = ctk.CTkLabel(sr, text="",
                                             font=ctk.CTkFont("Segoe UI", 11),
                                             text_color=_WHITE)
        self.lbl_cred_status.pack(side="left")
        self.lbl_balance = ctk.CTkLabel(sr, text="",
                                         font=ctk.CTkFont("Segoe UI", 11),
                                         text_color=_SUBTEXT)
        self.lbl_balance.pack(side="left", padx=(12, 0))

        ctk.CTkFrame(win, fg_color=_BORDER, height=1, corner_radius=0).pack(
            fill="x", padx=24, pady=12)

        ctk.CTkLabel(win, text="Gerar Áudios White Safe",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=_WHITE).pack(anchor="w", padx=24)
        ctk.CTkLabel(win,
                     text="Gera 32 áudios via MiniMax TTS (~$7, feito uma única vez).",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=_SUBTEXT).pack(anchor="w", padx=24, pady=(4, 10))

        sf = ctk.CTkFrame(win, fg_color=_PANEL, corner_radius=6)
        sf.pack(fill="x", padx=24)
        self.nicho_status_labels = {}
        nr = ctk.CTkFrame(sf, fg_color="transparent")
        nr.pack(fill="x", padx=12, pady=10)
        for niche in VALID_NICHES:
            col = ctk.CTkFrame(nr, fg_color="transparent")
            col.pack(side="left", expand=True)
            ctk.CTkLabel(col, text=NICHE_LABELS[niche],
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=_SUBTEXT).pack()
            lbl = ctk.CTkLabel(col, text="—",
                               font=ctk.CTkFont("Segoe UI", 11),
                               text_color=_SUBTEXT)
            lbl.pack()
            self.nicho_status_labels[niche] = lbl

        self.gen_log = ctk.CTkTextbox(win, fg_color=_PANEL, text_color="#CBD5E1",
                                       font=ctk.CTkFont("Consolas", 10),
                                       corner_radius=10, border_width=0,
                                       state="disabled", height=100)
        self.gen_log.pack(fill="both", expand=True, padx=24, pady=10)

        self.btn_gerar = ctk.CTkButton(
            win, text="GERAR ÁUDIOS  (~$7)", command=self._iniciar_geracao,
            fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            corner_radius=10, height=44)
        self.btn_gerar.pack(fill="x", padx=24, pady=(0, 16))

        api_key, group_id = load_credentials()
        if api_key: self.entry_apikey.insert(0, api_key)
        if group_id: self.entry_groupid.insert(0, group_id)

        self._atualizar_status_config()
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    # ── Lógica Configuração ──────────────────────────────────────────────────

    def _salvar_credenciais(self):
        api_key  = self.entry_apikey.get().strip()
        group_id = self.entry_groupid.get().strip()
        if not api_key or not group_id:
            messagebox.showwarning("Campos vazios", "Preencha a API Key e o Group ID.")
            return
        save_credentials(api_key, group_id)
        self._atualizar_status_config()
        messagebox.showinfo("Salvo", "Credenciais salvas!")

    def _testar_credenciais(self):
        api_key  = self.entry_apikey.get().strip()
        group_id = self.entry_groupid.get().strip()
        if not api_key or not group_id:
            messagebox.showwarning("Campos vazios", "Preencha os campos primeiro.")
            return
        self.lbl_cred_status.configure(text="Testando...", text_color=_WARNING)
        self.lbl_balance.configure(text="")
        def _test():
            ok, msg = test_credentials(api_key, group_id)
            def _update():
                self.lbl_cred_status.configure(
                    text=msg, text_color=_SUCCESS if ok else _ERROR)
                if ok:
                    balance = get_balance(api_key, group_id)
                    if balance:
                        self.lbl_balance.configure(text=f"  |  {balance}")
            self.after(0, _update)
        threading.Thread(target=_test, daemon=True).start()

    def _atualizar_status_config(self):
        try:
            api_key, group_id = load_credentials()
            if api_key and group_id:
                self.lbl_cred_status.configure(text="Credenciais salvas", text_color=_SUCCESS)
            else:
                self.lbl_cred_status.configure(text="Credenciais não configuradas", text_color=_WARNING)
            for niche in VALID_NICHES:
                count = len(list((WHITES_ROOT / niche).glob("*.wav")))
                if count >= 8:
                    self.nicho_status_labels[niche].configure(text=f"{count} áudios", text_color=_SUCCESS)
                elif count > 0:
                    self.nicho_status_labels[niche].configure(text=f"{count}/8 áudios", text_color=_WARNING)
                else:
                    self.nicho_status_labels[niche].configure(text="não gerado", text_color=_ERROR)
        except Exception:
            pass

    def _gen_log(self, msg):
        def _do():
            try:
                self.gen_log.configure(state="normal")
                self.gen_log.insert("end", msg + "\n")
                self.gen_log.see("end")
                self.gen_log.configure(state="disabled")
            except Exception:
                pass
        self.after(0, _do)

    def _iniciar_geracao(self):
        api_key, group_id = load_credentials()
        if not api_key or not group_id:
            messagebox.showerror("Sem credenciais", "Salve suas credenciais antes de gerar.")
            return
        try:
            self.btn_gerar.configure(state="disabled", text="Gerando...", fg_color="#555555")
            self.gen_log.configure(state="normal")
            self.gen_log.delete("0.0", "end")
            self.gen_log.configure(state="disabled")
        except Exception:
            pass
        threading.Thread(target=self._gerar_audios, args=(api_key, group_id), daemon=True).start()

    def _gerar_audios(self, api_key, group_id):
        total_ok = total_fail = 0
        for niche in VALID_NICHES:
            scripts_dir = SCRIPTS_ROOT / niche
            whites_dir  = WHITES_ROOT / niche
            scripts = sorted(scripts_dir.glob("*.txt")) if scripts_dir.exists() else []
            if not scripts:
                self._gen_log(f"[{niche}] Sem scripts encontrados.")
                continue
            self._gen_log(f"\n=== {NICHE_LABELS[niche]} ({len(scripts)} scripts) ===")
            for i, script_path in enumerate(scripts):
                out_wav = whites_dir / (script_path.stem + ".wav")
                if out_wav.exists():
                    self._gen_log(f"  >> {out_wav.name} (ja existe)")
                    continue
                voice = VOICE_POOL[i % len(VOICE_POOL)]
                text  = script_path.read_text(encoding="utf-8").strip()
                self._gen_log(f"  -> {script_path.name} ({voice})")
                try:
                    mp3_bytes = synthesize(text, voice, api_key, group_id)
                    mp3_to_wav(mp3_bytes, out_wav)
                    self._gen_log(f"     [OK] {out_wav.name}")
                    total_ok += 1
                except Exception as e:
                    self._gen_log(f"     [ERRO] {e}")
                    total_fail += 1
            self.after(0, self._atualizar_status_config)

        self._gen_log(f"\n=== Concluido: {total_ok} gerados, {total_fail} erros ===")
        def _reset_btn():
            try:
                self.btn_gerar.configure(state="normal", text="GERAR ÁUDIOS  (~$7)", fg_color=_ORANGE)
            except Exception:
                pass
        self.after(0, _reset_btn)
        self.after(0, self._atualizar_status_config)
        if total_fail == 0:
            self.after(0, lambda: messagebox.showinfo(
                "Concluido", f"{total_ok} audios gerados!\nJa pode processar videos."))

    # ── Lógica Processar ─────────────────────────────────────────────────────

    def _on_niche_change(self, label):
        niche = LABEL_TO_NICHE.get(label, "ed")
        self._set_niche(niche)

    def _set_niche(self, niche):
        self.niche_var.set(niche)
        save_config({"last_niche": niche})
        self.niche_seg.set(NICHE_LABELS[niche])

    def _on_drop(self, event):
        raw = event.data
        # tkinterdnd2 retorna paths entre chaves no Windows
        paths = self.tk.splitlist(raw)
        videos = sorted([Path(p) for p in paths
                         if Path(p).suffix.lower() in VIDEO_EXTS
                         and not Path(p).stem.endswith("_FaD")
                         and not Path(p).stem.endswith("_FaDW")])
        if not videos:
            messagebox.showwarning("Sem videos validos",
                                   "Nenhum video valido nos arquivos arrastados.")
            return
        self._carregar_videos(videos)

    def _selecionar_videos(self):
        arquivos = filedialog.askopenfilenames(
            title="Selecione os videos",
            filetypes=[("Videos", "*.mp4 *.mkv *.mov *.avi *.m4v *.webm"),
                       ("Todos os arquivos", "*.*")])
        if not arquivos:
            return
        videos = sorted([Path(f) for f in arquivos
                         if not Path(f).stem.endswith("_FaD")
                         and not Path(f).stem.endswith("_FaDW")])
        if not videos:
            messagebox.showwarning("Sem videos validos", "Nenhum video valido selecionado.")
            return
        self._carregar_videos(videos)

    def _carregar_videos(self, videos):
        self.videos = videos
        preview = " | ".join([v.name for v in videos[:3]])
        if len(videos) > 3:
            preview += f" ... (+{len(videos)-3})"
        self.lbl_contagem.configure(text=preview, text_color=_SUBTEXT)
        self.drop_zone.configure(text=f"  {len(videos)} vídeo(s) carregado(s)",
                                  text_color=_SUCCESS)
        self.btn_processar.configure(state="normal")
        self.progress.set(0)
        self.status_var.set(f"Pronto — {len(videos)} vídeo(s)")
        self._log_clear()
        self._log(f"{len(videos)} vídeo(s) selecionado(s):")
        for v in videos:
            self._log(f"  - {v.name}")

    def _limpar_videos(self):
        self.videos = []
        self.lbl_contagem.configure(text="", text_color=_SUBTEXT)
        self.drop_zone.configure(text="  📂  Arraste os vídeos aqui  ou  clique em Selecionar", text_color=_SUBTEXT)
        self.btn_processar.configure(state="disabled")
        self.btn_abrir.configure(state="disabled", fg_color=_BORDER, text_color=_SUBTEXT)
        self.progress.set(0)
        self.status_var.set("Aguardando...")
        self._log_clear()

    def _log(self, msg):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _do)

    def _log_clear(self):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.delete("0.0", "end")
            self.log_text.configure(state="disabled")
        self.after(0, _do)

    def _verificar_atualizacao(self):
        self.btn_update.configure(text="Verificando...", state="disabled")
        def _check():
            try:
                r = requests.get(VERSION_URL, timeout=10)
                data = r.json()
                latest = data.get("version", "0")
                changelog = data.get("changelog", "")
                download_url = data.get("download_url", "")
                def _show():
                    self.btn_update.configure(text="Verificar atualização", state="normal")
                    if latest > APP_VERSION:
                        if messagebox.askyesno(
                                "Atualização disponível!",
                                f"Versão atual: v{APP_VERSION}\n"
                                f"Nova versão: v{latest}\n\n"
                                f"{changelog}\n\n"
                                "Abrir página de download?"):
                            import webbrowser
                            webbrowser.open(download_url)
                    else:
                        messagebox.showinfo("Atualizado",
                                            f"Você já tem a versão mais recente (v{APP_VERSION}).")
                self.after(0, _show)
            except Exception:
                self.after(0, lambda: self.btn_update.configure(
                    text="Verificar atualização", state="normal"))
                self.after(0, lambda: messagebox.showwarning(
                    "Erro", "Não foi possível verificar atualizações.\nVerifique sua conexão."))
        threading.Thread(target=_check, daemon=True).start()

    def _check_ffmpeg(self):
        if not FFMPEG_BIN.exists() and shutil.which("ffmpeg") is None:
            messagebox.showerror("FFmpeg nao encontrado",
                                 "FFmpeg nao encontrado.\nInstale com: winget install ffmpeg")

    def _iniciar_processamento(self):
        if self.processando or not self.videos:
            return
        niche = self.niche_var.get()
        if not whites_ok(niche):
            if messagebox.askyesno(
                    "Audios nao gerados",
                    f"Os audios do nicho '{NICHE_LABELS[niche]}' ainda nao foram gerados.\n\n"
                    "Abrir Configuracoes MiniMax agora?"):
                self._open_config_modal()
            return
        self.processando = True
        self.btn_processar.configure(state="disabled", text="Processando...", fg_color="#555555")
        self._log_clear()
        self._log(f"Iniciando — Nicho: {NICHE_LABELS[niche]} — {len(self.videos)} vídeo(s)\n")
        self._progress_max = len(self.videos)
        self.progress.set(0)
        threading.Thread(target=self._processar, daemon=True).start()

    def _processar(self):
        import datetime
        niche = self.niche_var.get()
        ok = fail = skip = 0
        total = len(self.videos)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_log(f"\n=== {ts} | Nicho: {niche} | {total} video(s) ===")

        for i, video in enumerate(self.videos, 1):
            self.after(0, lambda s=f"Processando {i} de {total}: {video.name}":
                       self.status_var.set(s))
            self._log(f"[{i}/{total}] {video.name}")
            try:
                fad, fadw, skipped = process_one(video, niche)
                if skipped:
                    self._log(f"  [PULADO] ja processado")
                    save_log(f"  PULADO: {video.name}")
                    skip += 1
                else:
                    self._log(f"  [OK] {fad.name}")
                    self._log(f"  [OK] {fadw.name}")
                    save_log(f"  OK: {video.name}")
                    ok += 1
            except Exception as e:
                self._log(f"  [ERRO] {e}")
                save_log(f"  ERRO: {video.name} — {e}")
                fail += 1
            self.after(0, lambda v=i: self.progress.set(v / self._progress_max))

        resumo = f"\nConcluido: {ok} processado(s)"
        if skip:
            resumo += f", {skip} pulado(s)"
        if fail:
            resumo += f", {fail} com erro"
        self._log(resumo)
        save_log(resumo)

        if fail == 0:
            self.after(0, lambda: self.status_var.set(
                f"Concluido! {ok} processado(s)" + (f", {skip} pulado(s)" if skip else "")))
            self.after(0, lambda: self.lbl_status.configure(text_color=_SUCCESS))
            notify_windows("Phoenix PhaseCancel",
                           f"{ok} video(s) processado(s)" + (f", {skip} pulado(s)" if skip else ""))
        else:
            self.after(0, lambda: self.status_var.set(f"{ok} OK, {fail} com erro."))
            self.after(0, lambda: self.lbl_status.configure(text_color=_WARNING))
            notify_windows("Phoenix PhaseCancel", f"{ok} OK, {fail} com erro")

        self.processando = False
        self.after(0, lambda: self.btn_processar.configure(
            state="normal", text="PROCESSAR", fg_color=_ORANGE))
        if self.videos:
            pasta_proc = self.videos[0].parent / "processados"
            if pasta_proc.exists():
                self.after(0, lambda: self.btn_abrir.configure(
                    state="normal", fg_color=_BORDER, text_color=_WHITE))

    def _abrir_processados(self):
        if not self.videos:
            return
        pasta_proc = self.videos[0].parent / "processados"
        if pasta_proc.exists():
            subprocess.Popen(["explorer", str(pasta_proc)])
        else:
            messagebox.showinfo("Pasta nao encontrada", "Processe os videos primeiro.")

    # ── Aba Comprimir ────────────────────────────────────────────────────────

    def _build_tab_comprimir(self, frame):
        frame.configure(fg_color=_PANEL)
        frame.grid_rowconfigure(6, weight=1)   # linha do log expande
        frame.grid_columnconfigure(0, weight=1)
        self.comp_videos        = []
        self.comp_processando   = False
        self.comp_status_var    = tk.StringVar(value="Aguardando...")
        self._comp_progress_max = 1
        self.comp_out_var       = tk.StringVar(value="Mesma pasta do video")

        # GPU label — row 0
        self.lbl_gpu = ctk.CTkLabel(frame, text="Detectando encoder...",
                                     font=ctk.CTkFont("Segoe UI", 9, slant="italic"),
                                     text_color=_SUBTEXT)
        self.lbl_gpu.grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))
        def _detect():
            enc, name = get_gpu_encoder()
            def _show():
                if enc:
                    self.lbl_gpu.configure(
                        text=f"⚡  GPU {name} ({enc}) — aceleração de hardware ativa",
                        text_color=_SUCCESS)
                else:
                    self.lbl_gpu.configure(
                        text="CPU (libx264) — sem GPU compatível detectada",
                        text_color=_SUBTEXT)
            self.after(0, _show)
        threading.Thread(target=_detect, daemon=True).start()

        # Videos card — row 1
        vc = ctk.CTkFrame(frame, fg_color=_PANEL2, corner_radius=6)
        vc.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(vc, text="VÍDEOS",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16, pady=(8, 3))

        self.comp_drop_zone = ctk.CTkLabel(
            vc, text="  📂  Arraste os vídeos aqui  ou  clique em Selecionar",
            font=ctk.CTkFont("Segoe UI", 11), text_color=_SUBTEXT,
            fg_color=_BORDER, corner_radius=8, height=52, cursor="hand2")
        self.comp_drop_zone.pack(fill="x", padx=16, pady=(0, 8))
        self.comp_drop_zone.drop_target_register(DND_FILES)
        self.comp_drop_zone.dnd_bind("<<Drop>>", self._comp_on_drop)

        cbr = ctk.CTkFrame(vc, fg_color="transparent")
        cbr.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(cbr, text="Selecionar Vídeos", command=self._comp_selecionar,
                      fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      corner_radius=8, height=34).pack(side="left")
        ctk.CTkButton(cbr, text="Limpar", command=self._comp_limpar,
                      fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
                      font=ctk.CTkFont("Segoe UI", 11),
                      corner_radius=8, height=34).pack(side="left", padx=(8, 0))

        self.comp_lbl_contagem = ctk.CTkLabel(frame, text="",
                                               font=ctk.CTkFont("Segoe UI", 11),
                                               text_color=_SUBTEXT)
        self.comp_lbl_contagem.grid(row=2, column=0, sticky="w", padx=16, pady=(2, 0))

        # Qualidade card — row 3
        qc = ctk.CTkFrame(frame, fg_color=_PANEL2, corner_radius=6)
        qc.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        ctk.CTkLabel(qc, text="QUALIDADE",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=16, pady=(8, 4))
        self.comp_quality_var = tk.StringVar(value="Facebook Ads")
        self.comp_quality_seg = ctk.CTkSegmentedButton(
            qc, values=list(COMPRESS_PRESETS.keys()),
            command=self._comp_set_quality,
            fg_color=_BORDER, selected_color=_ORANGE,
            selected_hover_color=_ORANGE_HOVER,
            unselected_color=_BORDER, unselected_hover_color="#1E3566",
            text_color=_WHITE, font=ctk.CTkFont("Segoe UI", 11, "bold"),
            corner_radius=8, height=34)
        self.comp_quality_seg.pack(fill="x", padx=16, pady=(0, 8))
        self.comp_quality_seg.set("Facebook Ads")

        # Destino + Paralelos — row 4 (lado a lado para economizar espaço)
        row4 = ctk.CTkFrame(frame, fg_color="transparent")
        row4.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 6))
        row4.grid_columnconfigure(0, weight=3)
        row4.grid_columnconfigure(1, weight=1)

        dc = ctk.CTkFrame(row4, fg_color=_PANEL2, corner_radius=6)
        dc.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(dc, text="DESTINO",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=12, pady=(10, 4))
        dr = ctk.CTkFrame(dc, fg_color="transparent")
        dr.pack(fill="x", padx=12, pady=(0, 8))
        self.comp_dest_entry = ctk.CTkEntry(
            dr, fg_color=_BORDER, text_color=_WHITE, border_color=_BORDER,
            font=ctk.CTkFont("Segoe UI", 11), state="disabled", height=32, corner_radius=8)
        self.comp_dest_entry.pack(side="left", fill="x", expand=True)
        self._set_comp_dest("Mesma pasta do vídeo")
        ctk.CTkButton(dr, text="Escolher", command=self._comp_pick_output,
                      fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
                      font=ctk.CTkFont("Segoe UI", 11),
                      corner_radius=8, height=32, width=70).pack(side="left", padx=(6, 0))

        pc = ctk.CTkFrame(row4, fg_color=_PANEL2, corner_radius=6)
        pc.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(pc, text="PARALELOS",
                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=12, pady=(10, 4))
        self.comp_workers_var = tk.IntVar(value=2)
        self.comp_workers_seg = ctk.CTkSegmentedButton(
            pc, values=["1", "2", "3", "4"],
            command=lambda v: self.comp_workers_var.set(int(v)),
            fg_color=_BORDER, selected_color=_ORANGE,
            selected_hover_color=_ORANGE_HOVER,
            unselected_color=_BORDER, unselected_hover_color="#1E3566",
            text_color=_WHITE, font=ctk.CTkFont("Segoe UI", 11, "bold"),
            corner_radius=8, height=32)
        self.comp_workers_seg.pack(fill="x", padx=12, pady=(0, 6))
        self.comp_workers_seg.set("2")
        ctk.CTkLabel(pc, text="GPU: use 3 ou 4",
                     font=ctk.CTkFont("Segoe UI", 9, slant="italic"),
                     text_color=_SUBTEXT).pack(anchor="w", padx=12, pady=(0, 8))

        # Progress + status — row 5
        pf = ctk.CTkFrame(frame, fg_color="transparent")
        pf.grid(row=5, column=0, sticky="ew", padx=16, pady=(4, 0))
        self.comp_progress = ctk.CTkProgressBar(
            pf, fg_color=_BORDER, progress_color=_ORANGE,
            corner_radius=4, height=6)
        self.comp_progress.pack(fill="x")
        self.comp_progress.set(0)
        self.comp_lbl_status = ctk.CTkLabel(
            pf, text="Aguardando...",
            font=ctk.CTkFont("Segoe UI", 11), text_color=_SUBTEXT)
        self.comp_lbl_status.pack(anchor="w", pady=(3, 0))
        self.comp_status_var.trace_add("write", lambda *_: self.after(
            0, lambda: self.comp_lbl_status.configure(text=self.comp_status_var.get())))

        # Log — row 6 (expande)
        self.comp_log = ctk.CTkTextbox(
            frame, fg_color=_PANEL2, text_color="#CBD5E1",
            font=ctk.CTkFont("Consolas", 10), corner_radius=10,
            border_width=0, state="disabled", wrap="word")
        self.comp_log.grid(row=6, column=0, sticky="nsew", padx=16, pady=8)

        # Botões — row 7
        br = ctk.CTkFrame(frame, fg_color="transparent")
        br.grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.comp_btn = ctk.CTkButton(
            br, text="COMPRIMIR", command=self._comp_iniciar,
            fg_color=_ORANGE, hover_color=_ORANGE_HOVER,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            corner_radius=10, height=46, state="disabled")
        self.comp_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.comp_btn_abrir = ctk.CTkButton(
            br, text="Abrir Pasta", command=self._comp_abrir_pasta,
            fg_color=_BORDER, hover_color="#1E3566", text_color=_SUBTEXT,
            font=ctk.CTkFont("Segoe UI", 11),
            corner_radius=10, height=46, state="disabled")
        self.comp_btn_abrir.pack(side="left")

    def _set_comp_dest(self, value):
        self.comp_out_var.set(value)
        self.comp_dest_entry.configure(state="normal")
        self.comp_dest_entry.delete(0, "end")
        self.comp_dest_entry.insert(0, value)
        self.comp_dest_entry.configure(state="disabled")

    def _comp_set_quality(self, label):
        self.comp_quality_var.set(label)

    def _comp_on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        videos = sorted([Path(p) for p in paths
                         if Path(p).suffix.lower() in VIDEO_EXTS
                         and "_comprimido" not in Path(p).stem])
        if not videos:
            messagebox.showwarning("Sem videos validos", "Nenhum video valido nos arquivos arrastados.")
            return
        self._comp_carregar(videos)

    def _comp_selecionar(self):
        arquivos = filedialog.askopenfilenames(
            title="Selecione os videos",
            filetypes=[("Videos", "*.mp4 *.mkv *.mov *.avi *.m4v *.webm *.flv *.wmv"),
                       ("Todos", "*.*")])
        if not arquivos:
            return
        videos = sorted([Path(f) for f in arquivos
                         if "_comprimido" not in Path(f).stem])
        self._comp_carregar(videos)

    def _comp_carregar(self, videos):
        self.comp_videos = videos
        preview = " | ".join([v.name for v in videos[:3]])
        if len(videos) > 3:
            preview += f" ... (+{len(videos)-3})"
        self.comp_lbl_contagem.configure(text=preview, text_color=_SUBTEXT)
        self.comp_drop_zone.configure(text=f"  {len(videos)} vídeo(s) carregado(s)",
                                       text_color=_SUCCESS)
        self.comp_btn.configure(state="normal")
        self.comp_progress.set(0)
        self.comp_status_var.set(f"Pronto — {len(videos)} vídeo(s)")
        self._comp_log_clear()
        self._comp_log(f"{len(videos)} vídeo(s) selecionado(s):")
        for v in videos:
            self._comp_log(f"  - {v.name}")

    def _comp_limpar(self):
        self.comp_videos = []
        self.comp_lbl_contagem.configure(text="", text_color=_SUBTEXT)
        self.comp_drop_zone.configure(text="  📂  Arraste os vídeos aqui  ou  clique em Selecionar", text_color=_SUBTEXT)
        self.comp_btn.configure(state="disabled")
        self.comp_btn_abrir.configure(state="disabled", fg_color=_BORDER, text_color=_SUBTEXT)
        self.comp_progress.set(0)
        self.comp_status_var.set("Aguardando...")
        self._comp_log_clear()

    def _comp_pick_output(self):
        folder = filedialog.askdirectory(title="Pasta de destino")
        if folder:
            self._set_comp_dest(folder)

    def _comp_log(self, msg):
        def _do():
            self.comp_log.configure(state="normal")
            self.comp_log.insert("end", msg + "\n")
            self.comp_log.see("end")
            self.comp_log.configure(state="disabled")
        self.after(0, _do)

    def _comp_log_clear(self):
        def _do():
            self.comp_log.configure(state="normal")
            self.comp_log.delete("0.0", "end")
            self.comp_log.configure(state="disabled")
        self.after(0, _do)

    def _comp_output_path(self, src):
        dest = self.comp_out_var.get()
        folder = src.parent if dest in ("Mesma pasta do video", "Mesma pasta do vídeo") else Path(dest)
        out_dir = folder / "comprimidos"
        out_dir.mkdir(exist_ok=True)
        return out_dir / src.name

    def _comp_get_duration(self, path):
        """Usa ffmpeg para obter duracao do video (nao precisa de ffprobe)."""
        ff = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
        try:
            result = subprocess.run(
                [ff, "-i", str(path)],
                capture_output=True, text=True, timeout=15,
                creationflags=NO_WINDOW)
            # ffmpeg escreve info do arquivo no stderr
            output = result.stderr + result.stdout
            import re
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
            if m:
                h, m2, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return h * 3600 + m2 * 60 + s
        except Exception:
            pass
        return None

    def _comp_build_cmd(self, src, dest, settings, duration):
        ff = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
        gpu_enc, gpu_name = get_gpu_encoder()
        meta = ["-map_metadata", "-1", "-metadata", "title=",
                "-metadata", "comment=", "-metadata", "encoder="]

        if "target_mb" in settings:
            target_bits = settings["target_mb"] * 8 * 1024
            audio_kbps = 128
            video_kbps = max(100, int(target_bits / duration) - audio_kbps)
            if gpu_enc:
                return [ff, "-y", "-i", str(src),
                        "-vcodec", gpu_enc, "-b:v", f"{video_kbps}k",
                        "-acodec", "aac", "-b:a", f"{audio_kbps}k",
                        *meta, str(dest)]
            return [ff, "-y", "-i", str(src),
                    "-vcodec", "libx264", "-b:v", f"{video_kbps}k",
                    "-preset", "medium",
                    "-acodec", "aac", "-b:a", f"{audio_kbps}k",
                    *meta, str(dest)]

        if gpu_enc:
            qp = str(int(int(settings["crf"]) * 0.8))
            return [ff, "-y", "-i", str(src),
                    "-vcodec", gpu_enc, "-qp", qp,
                    "-acodec", "aac", "-b:a", "128k",
                    *meta, str(dest)]
        return [ff, "-y", "-i", str(src),
                "-vcodec", "libx264", "-crf", settings["crf"],
                "-preset", settings["preset"],
                "-acodec", "aac", "-b:a", "128k",
                *meta, str(dest)]

    def _comp_iniciar(self):
        if self.comp_processando or not self.comp_videos:
            return
        self.comp_processando = True
        self.comp_btn.configure(state="disabled", text="Comprimindo...", fg_color="#555555")
        self._comp_log_clear()
        workers = self.comp_workers_var.get()
        self._comp_log(f"Iniciando — {len(self.comp_videos)} vídeo(s) — {self.comp_quality_var.get()} — {workers} paralelo(s)\n")
        self._comp_progress_max = len(self.comp_videos)
        self.comp_progress.set(0)
        threading.Thread(target=self._comp_run, daemon=True).start()

    def _comp_run(self):
        import concurrent.futures
        settings  = COMPRESS_PRESETS[self.comp_quality_var.get()]
        total     = len(self.comp_videos)
        workers   = self.comp_workers_var.get()
        ok = fail = 0
        done = [0]
        lock = threading.Lock()
        last_dest = [None]

        def _run_cmd(cmd, src, dest, label=""):
            """Executa comando ffmpeg e retorna (sucesso, dest)."""
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        creationflags=NO_WINDOW)
                stdout, stderr = proc.communicate(timeout=3600)
                if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                    return True
                return False
            except Exception:
                return False

        def _process(src):
            self._comp_log(f"-> {src.name}")
            dest     = self._comp_output_path(src)
            duration = self._comp_get_duration(src)

            if duration is None and "target_mb" in settings:
                self._comp_log(f"  [ERRO] {src.name}: nao foi possivel ler duracao.")
                return False, None

            ff = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
            meta = ["-map_metadata", "-1", "-metadata", "title=",
                    "-metadata", "comment=", "-metadata", "encoder="]
            gpu_enc, gpu_name = get_gpu_encoder()

            # Monta comandos GPU e CPU
            if "target_mb" in settings:
                target_bits = settings["target_mb"] * 8 * 1024 * 0.93  # 7% margem pra overhead do container
                audio_kbps  = 128
                video_kbps  = max(100, int(target_bits / duration) - audio_kbps)
                cmd_gpu = [ff, "-y", "-i", str(src), "-vcodec", gpu_enc,
                           "-b:v", f"{video_kbps}k", "-acodec", "aac",
                           "-b:a", f"{audio_kbps}k", *meta, str(dest)] if gpu_enc else None
                cmd_cpu = [ff, "-y", "-i", str(src), "-vcodec", "libx264",
                           "-b:v", f"{video_kbps}k", "-preset", "medium",
                           "-acodec", "aac", "-b:a", f"{audio_kbps}k",
                           *meta, str(dest)]
            else:
                qp = str(int(int(settings["crf"]) * 0.8))
                cmd_gpu = [ff, "-y", "-i", str(src), "-vcodec", gpu_enc,
                           "-qp", qp, "-acodec", "aac", "-b:a", "128k",
                           *meta, str(dest)] if gpu_enc else None
                cmd_cpu = [ff, "-y", "-i", str(src), "-vcodec", "libx264",
                           "-crf", settings["crf"], "-preset", settings["preset"],
                           "-acodec", "aac", "-b:a", "128k", *meta, str(dest)]

            # Tenta GPU primeiro, fallback pra CPU
            if cmd_gpu and _run_cmd(cmd_gpu, src, dest):
                encoder_used = gpu_name
            elif _run_cmd(cmd_cpu, src, dest):
                encoder_used = "CPU"
            else:
                self._comp_log(f"  [ERRO] {src.name}: falha ao comprimir")
                return False, None

            orig  = src.stat().st_size / 1024 / 1024
            comp  = dest.stat().st_size / 1024 / 1024
            saved = (1 - comp / orig) * 100
            self._comp_log(f"  [OK] {src.name}: {orig:.1f}MB → {comp:.1f}MB ({saved:.0f}% menor) [{encoder_used}]")
            return True, dest

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_process, src): src for src in self.comp_videos}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    success, dest = fut.result()
                except Exception as e:
                    success, dest = False, None
                    self._comp_log(f"  [ERRO] Excecao inesperada: {e}")
                with lock:
                    done[0] += 1
                    if success:
                        ok += 1
                        last_dest[0] = dest
                    else:
                        fail += 1
                    v = done[0]
                self.after(0, lambda v=v: self.comp_progress.set(v / self._comp_progress_max))
                self.after(0, lambda v=v: self.comp_status_var.set(
                    f"Comprimindo... {v} de {total}"))

        resumo = f"\nConcluido: {ok} comprimido(s)"
        if fail:
            resumo += f", {fail} com erro"
        self._comp_log(resumo)

        if fail == 0:
            self.after(0, lambda: self.comp_status_var.set(f"Concluido! {ok} video(s) comprimido(s)"))
            self.after(0, lambda: notify_windows("Phoenix PhaseCancel",
                                                  f"Compressao concluida! {ok} video(s) prontos."))
        else:
            self.after(0, lambda: self.comp_status_var.set(f"{ok} OK, {fail} com erro"))
            self.after(0, lambda: notify_windows("Phoenix PhaseCancel",
                                                  f"{ok} comprimido(s), {fail} com erro."))

        self.comp_processando = False
        self.after(0, lambda: self.comp_btn.configure(
            state="normal", text="COMPRIMIR", fg_color=_ORANGE))
        if last_dest[0]:
            self.after(0, lambda: self.comp_btn_abrir.configure(
                state="normal", fg_color=_BORDER, text_color=_WHITE))

    def _comp_abrir_pasta(self):
        if not self.comp_videos:
            return
        dest = self.comp_out_var.get()
        folder = str(self.comp_videos[0].parent) if dest in (
            "Mesma pasta do video", "Mesma pasta do vídeo") else dest
        subprocess.Popen(["explorer", folder])


if __name__ == "__main__":
    app = App()
    app.mainloop()
    app.mainloop()
