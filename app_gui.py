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
from tkinterdnd2 import TkinterDnD, DND_FILES

# Oculta janela CMD em todos os subprocessos no Windows
NO_WINDOW = 0x08000000

# ── Versão ────────────────────────────────────────────────────────────────────
APP_VERSION = "1.5"
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

for n in ["ed", "diabetes", "emagrecimento", "neuropatia"]:
    (WHITES_ROOT / n).mkdir(parents=True, exist_ok=True)

# ── Configurações ─────────────────────────────────────────────────────────────
WHITE_GAIN_DB  = -28.0
SAMPLE_RATE    = 44100
AUDIO_BITRATE  = "128k"
VIDEO_EXTS     = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}
VALID_NICHES   = ["ed", "diabetes", "emagrecimento", "neuropatia"]
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


# ── Widgets auxiliares ────────────────────────────────────────────────────────

def make_btn(parent, text, command, bg=None, fg="white", font_size=10,
             bold=False, pady=8, padx=12, state="normal", width=None):
    bg = bg or COLORS["btn"]
    f = ("Segoe UI", font_size, "bold" if bold else "normal")
    btn = tk.Button(parent, text=text, command=command,
                    bg=bg, fg=fg, font=f, relief="flat",
                    cursor="hand2", pady=pady, padx=padx,
                    state=state, width=width)
    hover_bg = COLORS["btn_hover"] if bg == COLORS["btn"] else COLORS["panel"]
    btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg) if btn["state"] == "normal" else None)
    btn.bind("<Leave>", lambda e: btn.config(bg=bg) if btn["state"] == "normal" else None)
    return btn


# ── App Principal ─────────────────────────────────────────────────────────────

class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("Phoenix PhaseCancel")
        self.geometry("720x640")
        self.minsize(640, 580)
        self.configure(bg=COLORS["bg"])
        self.resizable(True, True)

        cfg = load_config()
        self.videos      = []
        self.pasta_atual = tk.StringVar(value="Nenhum video selecionado")
        self.niche_var   = tk.StringVar(value=cfg.get("last_niche", "ed"))
        self.status_var  = tk.StringVar(value="Aguardando...")
        self.processando = False

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=COLORS["border"], foreground=COLORS["subtext"],
                        padding=[16, 8], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["panel"])],
                  foreground=[("selected", COLORS["text"])])
        style.configure("TProgressbar",
                        troughcolor=COLORS["panel"],
                        background=COLORS["accent"], thickness=8)

        # Header
        tk.Frame(self, bg=COLORS["accent"], height=4).pack(fill="x")
        hf = tk.Frame(self, bg=COLORS["bg"], pady=14)
        hf.pack(fill="x", padx=30)
        tk.Label(hf, text="Phoenix PhaseCancel",
                 font=("Segoe UI", 20, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side="left", anchor="w")
        tk.Label(hf, text=f"v{APP_VERSION}",
                 font=("Segoe UI", 9),
                 bg=COLORS["bg"], fg=COLORS["subtext"]).pack(side="left", anchor="s", padx=(8, 0), pady=(0, 3))
        self.btn_update = make_btn(hf, "Verificar atualizacao",
                                   self._verificar_atualizacao,
                                   bg=COLORS["border"], fg=COLORS["subtext"],
                                   font_size=9, pady=4, padx=10)
        self.btn_update.pack(side="right", anchor="e")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self._build_tab_processar()
        self._build_tab_comprimir()

        self._check_ffmpeg()
        self._atualizar_status_config()

    # ── Aba Processar ────────────────────────────────────────────────────────

    def _build_tab_processar(self):
        frame = tk.Frame(self.nb, bg=COLORS["bg"])
        self.nb.add(frame, text="  Processar  ")

        # Drop zone + seleção
        pf = tk.Frame(frame, bg=COLORS["panel"], pady=16, padx=20)
        pf.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(pf, text="VIDEOS",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")

        # Drop zone
        self.drop_zone = tk.Label(pf,
                                  text="Arraste os videos aqui  ou",
                                  font=("Segoe UI", 10),
                                  bg=COLORS["border"], fg=COLORS["subtext"],
                                  pady=18, relief="flat", cursor="hand2")
        self.drop_zone.pack(fill="x", pady=(8, 0))
        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)

        btn_row_sel = tk.Frame(pf, bg=COLORS["panel"])
        btn_row_sel.pack(fill="x", pady=(8, 0))
        make_btn(btn_row_sel, "  Selecionar Videos  ", self._selecionar_videos,
                 pady=8, padx=12).pack(side="left")
        make_btn(btn_row_sel, "  Limpar  ", self._limpar_videos,
                 bg=COLORS["border"], fg=COLORS["subtext"],
                 pady=8, padx=12).pack(side="left", padx=(8, 0))

        self.lbl_contagem = tk.Label(frame, text="",
                                     font=("Segoe UI", 10),
                                     bg=COLORS["bg"], fg=COLORS["subtext"])
        self.lbl_contagem.pack(anchor="w", padx=20)

        # Nicho
        nf = tk.Frame(frame, bg=COLORS["panel"], pady=16, padx=20)
        nf.pack(fill="x", padx=20, pady=8)
        tk.Label(nf, text="NICHO",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        row2 = tk.Frame(nf, bg=COLORS["panel"])
        row2.pack(fill="x", pady=(8, 0))
        self.nicho_btns = {}
        for niche in VALID_NICHES:
            btn = tk.Button(row2, text=NICHE_LABELS[niche],
                            command=lambda n=niche: self._set_niche(n),
                            bg=COLORS["border"], fg=COLORS["subtext"],
                            font=("Segoe UI", 10), relief="flat",
                            cursor="hand2", padx=16, pady=8, width=12)
            btn.pack(side="left", padx=(0, 8))
            self.nicho_btns[niche] = btn
        self._set_niche(self.niche_var.get())

        # Link configuração MiniMax
        cfg_row = tk.Frame(nf, bg=COLORS["panel"])
        cfg_row.pack(fill="x", pady=(10, 0))
        tk.Button(cfg_row, text="  ⚙  Configurar MiniMax  ",
                  command=self._open_config_modal,
                  bg=COLORS["border"], fg=COLORS["subtext"],
                  font=("Segoe UI", 9), relief="flat",
                  cursor="hand2", bd=0,
                  activebackground=COLORS["bg"],
                  activeforeground=COLORS["text"]).pack(side="right")

        # Progresso
        pbar_frame = tk.Frame(frame, bg=COLORS["bg"])
        pbar_frame.pack(fill="x", padx=20, pady=(8, 0))
        self.progress = ttk.Progressbar(pbar_frame, mode="determinate")
        self.progress.pack(fill="x")
        self.lbl_status = tk.Label(frame, textvariable=self.status_var,
                                   font=("Segoe UI", 10),
                                   bg=COLORS["bg"], fg=COLORS["subtext"])
        self.lbl_status.pack(anchor="w", padx=20, pady=(4, 0))

        # Log
        lf = tk.Frame(frame, bg=COLORS["panel"])
        lf.pack(fill="both", expand=True, padx=20, pady=8)
        self.log_text = tk.Text(lf, height=6,
                                bg=COLORS["panel"], fg=COLORS["text"],
                                font=("Consolas", 9), relief="flat",
                                bd=0, state="disabled", wrap="word")
        sc = tk.Scrollbar(lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sc.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        sc.pack(side="right", fill="y")

        # Botões
        br = tk.Frame(frame, bg=COLORS["bg"])
        br.pack(fill="x", padx=20, pady=(0, 16))
        self.btn_processar = make_btn(br, "PROCESSAR",
                                      self._iniciar_processamento,
                                      font_size=13, bold=True,
                                      pady=14, state="disabled")
        self.btn_processar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_abrir = make_btn(br, "Abrir Processados",
                                  self._abrir_processados,
                                  bg=COLORS["border"], fg=COLORS["subtext"],
                                  pady=14, state="disabled")
        self.btn_abrir.pack(side="left")

    # ── Modal Configuração MiniMax ───────────────────────────────────────────

    def _open_config_modal(self):
        """Abre janela de configuração MiniMax (modal). Foca se já estiver aberta."""
        if hasattr(self, "_config_modal") and self._config_modal and \
                self._config_modal.winfo_exists():
            self._config_modal.lift()
            self._config_modal.focus_force()
            return

        win = tk.Toplevel(self)
        win.title("Configuracao MiniMax — Hack de Audio")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.geometry("520x640")
        win.grab_set()          # bloqueia a janela principal enquanto aberta
        self._config_modal = win

        # ── Título ────────────────────────────────────────────────────────────
        tk.Label(win, text="Credenciais MiniMax",
                 font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Label(win,
                 text="Crie sua conta em minimax.io/platform e cole as credenciais abaixo.",
                 font=("Segoe UI", 10),
                 bg=COLORS["bg"], fg=COLORS["subtext"]).pack(anchor="w", padx=24)

        cf = tk.Frame(win, bg=COLORS["panel"], pady=20, padx=20)
        cf.pack(fill="x", padx=24, pady=16)

        tk.Label(cf, text="API KEY",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        self.entry_apikey = tk.Entry(cf, font=("Segoe UI", 10),
                                     bg=COLORS["border"], fg=COLORS["text"],
                                     insertbackground="white", relief="flat", show="*")
        self.entry_apikey.pack(fill="x", pady=(4, 12), ipady=8)

        tk.Label(cf, text="GROUP ID  (numero longo 18-19 digitos)",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        self.entry_groupid = tk.Entry(cf, font=("Segoe UI", 10),
                                      bg=COLORS["border"], fg=COLORS["text"],
                                      insertbackground="white", relief="flat")
        self.entry_groupid.pack(fill="x", pady=(4, 0), ipady=8)

        btn_row_cred = tk.Frame(cf, bg=COLORS["panel"])
        btn_row_cred.pack(fill="x", pady=(16, 0))
        make_btn(btn_row_cred, "Salvar", self._salvar_credenciais,
                 pady=10).pack(side="left")
        make_btn(btn_row_cred, "Testar Credenciais", self._testar_credenciais,
                 bg=COLORS["border"], fg=COLORS["text"],
                 pady=10).pack(side="left", padx=(8, 0))

        # Status credenciais + saldo
        status_row = tk.Frame(win, bg=COLORS["bg"])
        status_row.pack(fill="x", padx=24)
        self.lbl_cred_status = tk.Label(status_row, text="",
                                        font=("Segoe UI", 10),
                                        bg=COLORS["bg"])
        self.lbl_cred_status.pack(side="left")
        self.lbl_balance = tk.Label(status_row, text="",
                                    font=("Segoe UI", 10),
                                    bg=COLORS["bg"], fg=COLORS["subtext"])
        self.lbl_balance.pack(side="left", padx=(16, 0))

        # Separador
        tk.Frame(win, bg=COLORS["border"], height=1).pack(fill="x", padx=24, pady=16)

        tk.Label(win, text="Gerar Audios White Safe",
                 font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w", padx=24)
        tk.Label(win,
                 text="Gera 32 audios via MiniMax TTS (~$7, feito uma unica vez).",
                 font=("Segoe UI", 10),
                 bg=COLORS["bg"], fg=COLORS["subtext"]).pack(anchor="w", padx=24, pady=(4, 12))

        # Status por nicho
        status_frame = tk.Frame(win, bg=COLORS["panel"], pady=12, padx=20)
        status_frame.pack(fill="x", padx=24)
        self.nicho_status_labels = {}
        row = tk.Frame(status_frame, bg=COLORS["panel"])
        row.pack(fill="x")
        for niche in VALID_NICHES:
            col = tk.Frame(row, bg=COLORS["panel"])
            col.pack(side="left", expand=True)
            tk.Label(col, text=NICHE_LABELS[niche],
                     font=("Segoe UI", 9, "bold"),
                     bg=COLORS["panel"], fg=COLORS["subtext"]).pack()
            lbl = tk.Label(col, text="—",
                           font=("Segoe UI", 9),
                           bg=COLORS["panel"], fg=COLORS["subtext"])
            lbl.pack()
            self.nicho_status_labels[niche] = lbl

        # Log geração
        lf2 = tk.Frame(win, bg=COLORS["panel"])
        lf2.pack(fill="both", expand=True, padx=24, pady=12)
        self.gen_log = tk.Text(lf2, height=5,
                               bg=COLORS["panel"], fg=COLORS["text"],
                               font=("Consolas", 9), relief="flat",
                               bd=0, state="disabled", wrap="word")
        sc2 = tk.Scrollbar(lf2, command=self.gen_log.yview)
        self.gen_log.configure(yscrollcommand=sc2.set)
        self.gen_log.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        sc2.pack(side="right", fill="y")

        self.btn_gerar = make_btn(win, "GERAR AUDIOS  (~$7)",
                                  self._iniciar_geracao,
                                  font_size=12, bold=True, pady=12)
        self.btn_gerar.pack(fill="x", padx=24, pady=(0, 16))

        # Carrega credenciais salvas
        api_key, group_id = load_credentials()
        if api_key:
            self.entry_apikey.insert(0, api_key)
        if group_id:
            self.entry_groupid.insert(0, group_id)

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
        self.lbl_cred_status.config(text="Testando...", fg=COLORS["warning"])
        self.lbl_balance.config(text="")
        def _test():
            ok, msg = test_credentials(api_key, group_id)
            def _update():
                self.lbl_cred_status.config(
                    text=msg,
                    fg=COLORS["success"] if ok else COLORS["error"])
                if ok:
                    balance = get_balance(api_key, group_id)
                    if balance:
                        self.lbl_balance.config(text=f"  |  {balance}")
            self.after(0, _update)
        threading.Thread(target=_test, daemon=True).start()

    def _atualizar_status_config(self):
        """Atualiza labels do modal de config (so se estiver aberto)."""
        try:
            api_key, group_id = load_credentials()
            if api_key and group_id:
                self.lbl_cred_status.config(text="Credenciais salvas", fg=COLORS["success"])
            else:
                self.lbl_cred_status.config(text="Credenciais nao configuradas", fg=COLORS["warning"])
            for niche in VALID_NICHES:
                count = len(list((WHITES_ROOT / niche).glob("*.wav")))
                if count >= 8:
                    self.nicho_status_labels[niche].config(text=f"{count} audios", fg=COLORS["success"])
                elif count > 0:
                    self.nicho_status_labels[niche].config(text=f"{count}/8 audios", fg=COLORS["warning"])
                else:
                    self.nicho_status_labels[niche].config(text="nao gerado", fg=COLORS["error"])
        except Exception:
            pass

    def _gen_log(self, msg):
        def _do():
            try:
                self.gen_log.config(state="normal")
                self.gen_log.insert("end", msg + "\n")
                self.gen_log.see("end")
                self.gen_log.config(state="disabled")
            except Exception:
                pass
        self.after(0, _do)

    def _iniciar_geracao(self):
        api_key, group_id = load_credentials()
        if not api_key or not group_id:
            messagebox.showerror("Sem credenciais", "Salve suas credenciais antes de gerar.")
            return
        try:
            self.btn_gerar.config(state="disabled", text="Gerando...", bg="#555555")
            self.gen_log.config(state="normal")
            self.gen_log.delete("1.0", "end")
            self.gen_log.config(state="disabled")
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
                self.btn_gerar.config(state="normal", text="GERAR AUDIOS  (~$7)", bg=COLORS["btn"])
            except Exception:
                pass
        self.after(0, _reset_btn)
        self.after(0, self._atualizar_status_config)
        if total_fail == 0:
            self.after(0, lambda: messagebox.showinfo(
                "Concluido", f"{total_ok} audios gerados!\nJa pode processar videos."))

    # ── Lógica Processar ─────────────────────────────────────────────────────

    def _set_niche(self, niche):
        self.niche_var.set(niche)
        save_config({"last_niche": niche})
        for n, btn in self.nicho_btns.items():
            if n == niche:
                btn.config(bg=COLORS["accent"], fg="white",
                           font=("Segoe UI", 10, "bold"))
            else:
                btn.config(bg=COLORS["border"], fg=COLORS["subtext"],
                           font=("Segoe UI", 10))

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
        self.pasta_atual.set(f"{len(videos)} video(s) selecionado(s)")
        preview = " | ".join([v.name for v in videos[:3]])
        if len(videos) > 3:
            preview += f" ... (+{len(videos)-3})"
        self.lbl_contagem.config(text=preview, fg=COLORS["subtext"])
        self.drop_zone.config(text=f"{len(videos)} video(s) carregado(s)",
                              fg=COLORS["success"])
        self.btn_processar.config(state="normal")
        self.progress["value"] = 0
        self.status_var.set(f"Pronto — {len(videos)} video(s)")
        self._log_clear()
        self._log(f"{len(videos)} video(s) selecionado(s):")
        for v in videos:
            self._log(f"  - {v.name}")

    def _limpar_videos(self):
        self.videos = []
        self.pasta_atual.set("Nenhum video selecionado")
        self.lbl_contagem.config(text="", fg=COLORS["subtext"])
        self.drop_zone.config(text="Arraste os videos aqui  ou", fg=COLORS["subtext"])
        self.btn_processar.config(state="disabled")
        self.btn_abrir.config(state="disabled", bg=COLORS["border"], fg=COLORS["subtext"])
        self.progress["value"] = 0
        self.status_var.set("Aguardando...")
        self._log_clear()

    def _log(self, msg):
        def _do():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _do)

    def _log_clear(self):
        def _do():
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.config(state="disabled")
        self.after(0, _do)

    def _verificar_atualizacao(self):
        self.btn_update.config(text="Verificando...", state="disabled")
        def _check():
            try:
                r = requests.get(VERSION_URL, timeout=10)
                data = r.json()
                latest = data.get("version", "0")
                changelog = data.get("changelog", "")
                download_url = data.get("download_url", "")
                def _show():
                    self.btn_update.config(text="Verificar atualizacao", state="normal")
                    if latest > APP_VERSION:
                        if messagebox.askyesno(
                                "Atualizacao disponivel!",
                                f"Versao atual: v{APP_VERSION}\n"
                                f"Nova versao: v{latest}\n\n"
                                f"{changelog}\n\n"
                                "Abrir pagina de download?"):
                            import webbrowser
                            webbrowser.open(download_url)
                    else:
                        messagebox.showinfo("Atualizado",
                                            f"Voce ja tem a versao mais recente (v{APP_VERSION}).")
                self.after(0, _show)
            except Exception:
                self.after(0, lambda: self.btn_update.config(
                    text="Verificar atualizacao", state="normal"))
                self.after(0, lambda: messagebox.showwarning(
                    "Erro", "Nao foi possivel verificar atualizacoes.\nVerifique sua conexao."))
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
        self.btn_processar.config(state="disabled", text="Processando...", bg="#555555")
        self._log_clear()
        self._log(f"Iniciando — Nicho: {NICHE_LABELS[niche]} — {len(self.videos)} video(s)\n")
        self.progress["maximum"] = len(self.videos)
        self.progress["value"]   = 0
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
            self.after(0, lambda v=i: self.progress.configure(value=v))

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
            self.after(0, lambda: self.lbl_status.config(fg=COLORS["success"]))
            notify_windows("Phoenix PhaseCancel",
                           f"{ok} video(s) processado(s)" + (f", {skip} pulado(s)" if skip else ""))
        else:
            self.after(0, lambda: self.status_var.set(f"{ok} OK, {fail} com erro."))
            self.after(0, lambda: self.lbl_status.config(fg=COLORS["warning"]))
            notify_windows("Phoenix PhaseCancel", f"{ok} OK, {fail} com erro")

        self.processando = False
        self.after(0, lambda: self.btn_processar.config(
            state="normal", text="PROCESSAR", bg=COLORS["btn"]))
        if self.videos:
            pasta_proc = self.videos[0].parent / "processados"
            if pasta_proc.exists():
                self.after(0, lambda: self.btn_abrir.config(
                    state="normal", bg=COLORS["panel"], fg=COLORS["text"]))

    def _abrir_processados(self):
        if not self.videos:
            return
        pasta_proc = self.videos[0].parent / "processados"
        if pasta_proc.exists():
            subprocess.Popen(["explorer", str(pasta_proc)])
        else:
            messagebox.showinfo("Pasta nao encontrada", "Processe os videos primeiro.")

    # ── Aba Comprimir ────────────────────────────────────────────────────────

    def _build_tab_comprimir(self):
        frame = tk.Frame(self.nb, bg=COLORS["bg"])
        self.nb.add(frame, text="  Comprimir  ")

        self.comp_videos = []
        self.comp_processando = False
        self.comp_status_var = tk.StringVar(value="Aguardando...")

        # Detecta GPU em background e mostra no topo
        gpu_row = tk.Frame(frame, bg=COLORS["bg"])
        gpu_row.pack(fill="x", padx=20, pady=(10, 0))
        self.lbl_gpu = tk.Label(gpu_row, text="Detectando encoder...",
                                font=("Segoe UI", 9, "italic"),
                                bg=COLORS["bg"], fg=COLORS["subtext"])
        self.lbl_gpu.pack(anchor="w")
        def _detect():
            enc, name = get_gpu_encoder()
            def _show():
                if enc:
                    self.lbl_gpu.config(
                        text=f"Encoder: GPU {name} ({enc}) — aceleracao de hardware ativa",
                        fg=COLORS["success"])
                else:
                    self.lbl_gpu.config(
                        text="Encoder: CPU (libx264) — sem GPU compativel detectada",
                        fg=COLORS["subtext"])
            self.after(0, _show)
        threading.Thread(target=_detect, daemon=True).start()

        # Drop zone
        pf = tk.Frame(frame, bg=COLORS["panel"], pady=16, padx=20)
        pf.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(pf, text="VIDEOS",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")

        self.comp_drop_zone = tk.Label(pf,
                                       text="Arraste os videos aqui  ou",
                                       font=("Segoe UI", 10),
                                       bg=COLORS["border"], fg=COLORS["subtext"],
                                       pady=18, relief="flat", cursor="hand2")
        self.comp_drop_zone.pack(fill="x", pady=(8, 0))
        self.comp_drop_zone.drop_target_register(DND_FILES)
        self.comp_drop_zone.dnd_bind("<<Drop>>", self._comp_on_drop)

        btn_row = tk.Frame(pf, bg=COLORS["panel"])
        btn_row.pack(fill="x", pady=(8, 0))
        make_btn(btn_row, "  Selecionar Videos  ", self._comp_selecionar,
                 pady=8, padx=12).pack(side="left")
        make_btn(btn_row, "  Limpar  ", self._comp_limpar,
                 bg=COLORS["border"], fg=COLORS["subtext"],
                 pady=8, padx=12).pack(side="left", padx=(8, 0))

        self.comp_lbl_contagem = tk.Label(frame, text="",
                                          font=("Segoe UI", 10),
                                          bg=COLORS["bg"], fg=COLORS["subtext"])
        self.comp_lbl_contagem.pack(anchor="w", padx=20)

        # Qualidade
        qf = tk.Frame(frame, bg=COLORS["panel"], pady=16, padx=20)
        qf.pack(fill="x", padx=20, pady=8)
        tk.Label(qf, text="QUALIDADE",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        row_q = tk.Frame(qf, bg=COLORS["panel"])
        row_q.pack(fill="x", pady=(8, 0))
        self.comp_quality_var = tk.StringVar(value="Balanceado")
        self.comp_quality_btns = {}
        for label in COMPRESS_PRESETS:
            btn = tk.Button(row_q, text=label,
                            command=lambda l=label: self._comp_set_quality(l),
                            bg=COLORS["border"], fg=COLORS["subtext"],
                            font=("Segoe UI", 10), relief="flat",
                            cursor="hand2", padx=16, pady=8)
            btn.pack(side="left", padx=(0, 8))
            self.comp_quality_btns[label] = btn
        self._comp_set_quality("Facebook Ads")

        # Destino
        df = tk.Frame(frame, bg=COLORS["panel"], pady=12, padx=20)
        df.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(df, text="DESTINO",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        dest_row = tk.Frame(df, bg=COLORS["panel"])
        dest_row.pack(fill="x", pady=(8, 0))
        self.comp_out_var = tk.StringVar(value="Mesma pasta do video")
        tk.Entry(dest_row, textvariable=self.comp_out_var,
                 bg=COLORS["border"], fg=COLORS["text"],
                 readonlybackground=COLORS["border"],
                 disabledbackground=COLORS["border"],
                 disabledforeground=COLORS["text"],
                 insertbackground="white", relief="flat",
                 font=("Segoe UI", 10), state="readonly").pack(side="left", fill="x", expand=True, ipady=6)
        make_btn(dest_row, "Escolher", self._comp_pick_output,
                 bg=COLORS["border"], fg=COLORS["subtext"],
                 pady=6, padx=10).pack(side="left", padx=(8, 0))

        # Progresso
        pbar_frame = tk.Frame(frame, bg=COLORS["bg"])
        pbar_frame.pack(fill="x", padx=20, pady=(8, 0))
        self.comp_progress = ttk.Progressbar(pbar_frame, mode="determinate")
        self.comp_progress.pack(fill="x")
        tk.Label(frame, textvariable=self.comp_status_var,
                 font=("Segoe UI", 10),
                 bg=COLORS["bg"], fg=COLORS["subtext"]).pack(anchor="w", padx=20, pady=(4, 0))

        # Log
        lf = tk.Frame(frame, bg=COLORS["panel"])
        lf.pack(fill="both", expand=True, padx=20, pady=8)
        self.comp_log = tk.Text(lf, height=5,
                                bg=COLORS["panel"], fg=COLORS["text"],
                                font=("Consolas", 9), relief="flat",
                                bd=0, state="disabled", wrap="word")
        sc = tk.Scrollbar(lf, command=self.comp_log.yview)
        self.comp_log.configure(yscrollcommand=sc.set)
        self.comp_log.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        sc.pack(side="right", fill="y")

        # Paralelos
        par_frame = tk.Frame(frame, bg=COLORS["panel"], pady=12, padx=20)
        par_frame.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(par_frame, text="VIDEOS SIMULTANEOS",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w")
        par_row = tk.Frame(par_frame, bg=COLORS["panel"])
        par_row.pack(anchor="w", pady=(6, 0))
        self.comp_workers_var = tk.IntVar(value=2)
        self.comp_workers_btns = {}
        for n in [1, 2, 3, 4]:
            btn = tk.Button(par_row, text=str(n),
                            command=lambda v=n: self._comp_set_workers(v),
                            bg=COLORS["border"], fg=COLORS["subtext"],
                            font=("Segoe UI", 10), relief="flat",
                            cursor="hand2", padx=16, pady=6, width=4)
            btn.pack(side="left", padx=(0, 6))
            self.comp_workers_btns[n] = btn
        self._comp_set_workers(2)
        tk.Label(par_frame, text="Recomendado: 2. Com GPU, pode usar 3 ou 4.",
                 font=("Segoe UI", 8, "italic"),
                 bg=COLORS["panel"], fg=COLORS["subtext"]).pack(anchor="w", pady=(4, 0))

        # Botões
        br = tk.Frame(frame, bg=COLORS["bg"])
        br.pack(fill="x", padx=20, pady=(0, 16))
        self.comp_btn = make_btn(br, "COMPRIMIR",
                                 self._comp_iniciar,
                                 font_size=13, bold=True,
                                 pady=14, state="disabled")
        self.comp_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.comp_btn_abrir = make_btn(br, "Abrir Pasta",
                                       self._comp_abrir_pasta,
                                       bg=COLORS["border"], fg=COLORS["subtext"],
                                       pady=14, state="disabled")
        self.comp_btn_abrir.pack(side="left")

    def _comp_set_workers(self, n):
        self.comp_workers_var.set(n)
        for v, btn in self.comp_workers_btns.items():
            if v == n:
                btn.config(bg=COLORS["accent"], fg="white", font=("Segoe UI", 10, "bold"))
            else:
                btn.config(bg=COLORS["border"], fg=COLORS["subtext"], font=("Segoe UI", 10))

    def _comp_set_quality(self, label):
        self.comp_quality_var.set(label)
        for l, btn in self.comp_quality_btns.items():
            if l == label:
                btn.config(bg=COLORS["accent"], fg="white",
                           font=("Segoe UI", 10, "bold"))
            else:
                btn.config(bg=COLORS["border"], fg=COLORS["subtext"],
                           font=("Segoe UI", 10))

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
        self.comp_lbl_contagem.config(text=preview, fg=COLORS["subtext"])
        self.comp_drop_zone.config(text=f"{len(videos)} video(s) carregado(s)", fg=COLORS["success"])
        self.comp_btn.config(state="normal")
        self.comp_progress["value"] = 0
        self.comp_status_var.set(f"Pronto — {len(videos)} video(s)")
        self._comp_log_clear()
        self._comp_log(f"{len(videos)} video(s) selecionado(s):")
        for v in videos:
            self._comp_log(f"  - {v.name}")

    def _comp_limpar(self):
        self.comp_videos = []
        self.comp_lbl_contagem.config(text="", fg=COLORS["subtext"])
        self.comp_drop_zone.config(text="Arraste os videos aqui  ou", fg=COLORS["subtext"])
        self.comp_btn.config(state="disabled")
        self.comp_btn_abrir.config(state="disabled", bg=COLORS["border"], fg=COLORS["subtext"])
        self.comp_progress["value"] = 0
        self.comp_status_var.set("Aguardando...")
        self._comp_log_clear()

    def _comp_pick_output(self):
        folder = filedialog.askdirectory(title="Pasta de destino")
        if folder:
            self.comp_out_var.set(folder)

    def _comp_log(self, msg):
        def _do():
            self.comp_log.config(state="normal")
            self.comp_log.insert("end", msg + "\n")
            self.comp_log.see("end")
            self.comp_log.config(state="disabled")
        self.after(0, _do)

    def _comp_log_clear(self):
        def _do():
            self.comp_log.config(state="normal")
            self.comp_log.delete("1.0", "end")
            self.comp_log.config(state="disabled")
        self.after(0, _do)

    def _comp_output_path(self, src):
        dest = self.comp_out_var.get()
        folder = src.parent if dest == "Mesma pasta do video" else Path(dest)
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
        self.comp_btn.config(state="disabled", text="Comprimindo...", bg="#555555")
        self._comp_log_clear()
        workers = self.comp_workers_var.get()
        self._comp_log(f"Iniciando — {len(self.comp_videos)} video(s) — {self.comp_quality_var.get()} — {workers} paralelo(s)\n")
        self.comp_progress["maximum"] = len(self.comp_videos)
        self.comp_progress["value"] = 0
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
                self.after(0, lambda v=v: self.comp_progress.configure(value=v))
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
        self.after(0, lambda: self.comp_btn.config(
            state="normal", text="COMPRIMIR", bg=COLORS["btn"]))

        if last_dest[0]:
            self.after(0, lambda: self.comp_btn_abrir.config(
                state="normal", bg=COLORS["panel"], fg=COLORS["text"]))

    def _comp_abrir_pasta(self):
        if not self.comp_videos:
            return
        dest = self.comp_out_var.get()
        folder = str(self.comp_videos[0].parent) if dest == "Mesma pasta do video" else dest
        subprocess.Popen(["explorer", folder])


if __name__ == "__main__":
    app = App()
    app.mainloop()
