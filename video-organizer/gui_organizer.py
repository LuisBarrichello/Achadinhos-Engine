"""
=============================================================
 Achadinhos do Momento — GUI Video Organizer
 Interface gráfica para organizar vídeos sem usar o terminal
 Biblioteca: CustomTkinter (pip install customtkinter)
=============================================================
Como usar:
  pip install customtkinter
  python gui_organizer.py
=============================================================
"""

import csv
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ─── Tema global ─────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ─── Configurações ────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "./organized"))
QUEUE_CSV  = Path(os.getenv("QUEUE_CSV", "./queue.csv"))

STORES = ["shopee", "mercadolivre", "outros"]
BADGES = ["HOT", "TOP", "OFERTA", "NOVO", "EXCLUSIVO", "LIMITADO", ""]

# ─── Paleta de cores ─────────────────────────────────────────
COLOR = {
    "bg":           "#0a0a0b",
    "surface":      "#111113",
    "surface_high": "#18181b",
    "border":       "#27272a",
    "text":         "#e4e4e7",
    "muted":        "#71717a",
    "primary":      "#7c3aed",
    "primary_hover":"#6d28d9",
    "success":      "#16a34a",
    "error":        "#dc2626",
    "warning":      "#d97706",
    "shopee":       "#ee4d2d",
    "mercadolivre": "#ffe600",
    "outros":       "#a78bfa",
}


# ─── Helper: slugify ──────────────────────────────────────────
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "_", text).strip("_")


# ─── Helper: salvar no CSV ────────────────────────────────────
def save_to_csv(row: dict) -> None:
    fieldnames = ["video_path", "title", "store", "badge", "url", "status"]
    file_exists = QUEUE_CSV.exists()
    with open(QUEUE_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ─── Helper: copiar e renomear vídeo ──────────────────────────
def organize_video(src_path: Path, title: str, store: str) -> Path:
    today     = datetime.now().strftime("%Y-%m-%d")
    slug      = slugify(title)
    store_dir = OUTPUT_DIR / store
    store_dir.mkdir(parents=True, exist_ok=True)
    dest      = store_dir / f"{today}_{slug}{src_path.suffix}"
    shutil.copy2(src_path, dest)
    return dest


# ══════════════════════════════════════════════════════════════
# JANELA PRINCIPAL
# ══════════════════════════════════════════════════════════════

class App(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("Achadinhos — Video Organizer")
        self.geometry("620x760")
        self.minsize(580, 680)
        self.configure(fg_color=COLOR["bg"])
        self.resizable(True, True)

        # ── Estado ────────────────────────────────────────────
        self.selected_video: Path | None = None
        self.recent_items: list[dict]    = []  # histórico da sessão

        # ── Construção da UI ──────────────────────────────────
        self._build_header()
        self._build_form()
        self._build_actions()
        self._build_status()
        self._build_history()

        self._log("Pronto. Selecione um vídeo para começar.", "muted")

    # ── Header ────────────────────────────────────────────────
    def _build_header(self):
        frame = ctk.CTkFrame(self, fg_color=COLOR["surface"], corner_radius=0,
                             border_width=0, height=64)
        frame.pack(fill="x")
        frame.pack_propagate(False)

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=12)

        ctk.CTkLabel(inner, text="🛍️  Video Organizer",
                     font=ctk.CTkFont(family="", size=16, weight="bold"),
                     text_color=COLOR["text"]).pack(side="left")

        ctk.CTkLabel(inner, text="Achadinhos do Momento",
                     font=ctk.CTkFont(size=11),
                     text_color=COLOR["muted"]).pack(side="right")

    # ── Formulário ────────────────────────────────────────────
    def _build_form(self):
        wrapper = ctk.CTkFrame(self, fg_color="transparent")
        wrapper.pack(fill="x", padx=20, pady=(20, 0))

        # Título
        self._label(wrapper, "Título da Oferta *")
        self.entry_title = self._entry(wrapper, "Ex: Tênis Nike Air Max — 60% OFF")
        self.entry_title.pack(fill="x", pady=(4, 14))

        # URL
        self._label(wrapper, "URL de Afiliado *")
        self.entry_url = self._entry(wrapper, "https://shopee.com.br/...")
        self.entry_url.pack(fill="x", pady=(4, 14))

        # Linha: Loja + Badge
        row1 = ctk.CTkFrame(wrapper, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 14))
        row1.columnconfigure(0, weight=1)
        row1.columnconfigure(1, weight=1)

        col_store = ctk.CTkFrame(row1, fg_color="transparent")
        col_store.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._label(col_store, "Loja")
        self.combo_store = ctk.CTkComboBox(
            col_store, values=STORES, state="readonly",
            fg_color=COLOR["surface_high"], border_color=COLOR["border"],
            button_color=COLOR["border"], button_hover_color=COLOR["primary"],
            text_color=COLOR["text"], dropdown_fg_color=COLOR["surface"],
            dropdown_hover_color=COLOR["surface_high"],
            font=ctk.CTkFont(size=13),
            command=self._on_store_change,
        )
        self.combo_store.set("shopee")
        self.combo_store.pack(fill="x", pady=(4, 0))

        col_badge = ctk.CTkFrame(row1, fg_color="transparent")
        col_badge.grid(row=0, column=1, sticky="ew")
        self._label(col_badge, "Badge")
        self.combo_badge = ctk.CTkComboBox(
            col_badge, values=BADGES,
            fg_color=COLOR["surface_high"], border_color=COLOR["border"],
            button_color=COLOR["border"], button_hover_color=COLOR["primary"],
            text_color=COLOR["text"], dropdown_fg_color=COLOR["surface"],
            dropdown_hover_color=COLOR["surface_high"],
            font=ctk.CTkFont(size=13),
        )
        self.combo_badge.set("HOT")
        self.combo_badge.pack(fill="x", pady=(4, 0))

        # Seleção de vídeo
        self._label(wrapper, "Arquivo de Vídeo *")
        video_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        video_row.pack(fill="x", pady=(4, 0))

        self.lbl_video = ctk.CTkLabel(
            video_row,
            text="Nenhum arquivo selecionado",
            text_color=COLOR["muted"],
            font=ctk.CTkFont(size=12),
            anchor="w",
            fg_color=COLOR["surface_high"],
            corner_radius=8,
        )
        self.lbl_video.pack(side="left", fill="x", expand=True, ipady=8, ipadx=10, padx=(0, 8))

        ctk.CTkButton(
            video_row, text="📂  Selecionar",
            width=120, height=38,
            fg_color=COLOR["surface_high"],
            hover_color=COLOR["border"],
            border_color=COLOR["border"], border_width=1,
            text_color=COLOR["text"],
            font=ctk.CTkFont(size=13),
            command=self._pick_video,
        ).pack(side="right")

    # ── Botão principal ────────────────────────────────────────
    def _build_actions(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=20, pady=20)

        self.btn_organize = ctk.CTkButton(
            frame,
            text="⚡  Organizar e Preparar",
            height=52,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR["primary"],
            hover_color=COLOR["primary_hover"],
            corner_radius=12,
            command=self._run,
        )
        self.btn_organize.pack(fill="x")

        # Barra de progresso (oculta inicialmente)
        self.progress = ctk.CTkProgressBar(
            frame, height=3, corner_radius=99,
            fg_color=COLOR["surface_high"],
            progress_color=COLOR["primary"],
        )
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.pack_forget()

    # ── Status ────────────────────────────────────────────────
    def _build_status(self):
        frame = ctk.CTkFrame(self,
                             fg_color=COLOR["surface"],
                             corner_radius=12,
                             border_width=1,
                             border_color=COLOR["border"])
        frame.pack(fill="x", padx=20, pady=(0, 12))

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(header, text="Log de Status",
                     font=ctk.CTkFont(size=11),
                     text_color=COLOR["muted"]).pack(side="left")

        ctk.CTkButton(header, text="Limpar",
                      width=50, height=20,
                      fg_color="transparent",
                      hover_color=COLOR["surface_high"],
                      text_color=COLOR["muted"],
                      font=ctk.CTkFont(size=10),
                      command=self._clear_log).pack(side="right")

        self.txt_log = ctk.CTkTextbox(
            frame, height=100,
            fg_color="transparent",
            text_color=COLOR["text"],
            font=ctk.CTkFont(family="Courier New" if sys.platform=="win32" else "Menlo", size=12),
            border_width=0,
            activate_scrollbars=True,
            wrap="word",
        )
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.txt_log.configure(state="disabled")

    # ── Histórico da sessão ───────────────────────────────────
    def _build_history(self):
        frame = ctk.CTkFrame(self,
                             fg_color=COLOR["surface"],
                             corner_radius=12,
                             border_width=1,
                             border_color=COLOR["border"])
        frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(header, text="Organizados Nesta Sessão",
                     font=ctk.CTkFont(size=11),
                     text_color=COLOR["muted"]).pack(side="left")
        self.lbl_count = ctk.CTkLabel(header, text="0 vídeos",
                                       font=ctk.CTkFont(size=11),
                                       text_color=COLOR["muted"])
        self.lbl_count.pack(side="right")

        self.history_frame = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", scrollbar_button_color=COLOR["border"])
        self.history_frame.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        self.lbl_empty_history = ctk.CTkLabel(
            self.history_frame,
            text="Nenhum vídeo organizado ainda.",
            text_color=COLOR["muted"], font=ctk.CTkFont(size=12),
        )
        self.lbl_empty_history.pack(pady=20)

    # ── Helpers de UI ─────────────────────────────────────────
    def _label(self, parent, text: str):
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont(size=12),
                     text_color=COLOR["muted"],
                     anchor="w").pack(fill="x")

    def _entry(self, parent, placeholder: str) -> ctk.CTkEntry:
        return ctk.CTkEntry(
            parent,
            placeholder_text=placeholder,
            fg_color=COLOR["surface_high"],
            border_color=COLOR["border"],
            text_color=COLOR["text"],
            placeholder_text_color=COLOR["muted"],
            font=ctk.CTkFont(size=13),
            height=38,
        )

    def _log(self, msg: str, level: str = "text"):
        colors = {
            "text":    COLOR["text"],
            "muted":   COLOR["muted"],
            "success": COLOR["success"],
            "error":   COLOR["error"],
            "warning": COLOR["warning"],
        }
        prefix_map = {"error": "✗ ", "success": "✓ ", "warning": "⚠ ", "muted": "  "}
        prefix = prefix_map.get(level, "→ ")
        ts  = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {prefix}{msg}\n"

        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line)
        self.txt_log.configure(state="disabled")
        self.txt_log.see("end")

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _on_store_change(self, val):
        pass  # pode adicionar visual feedback aqui

    # ── Ação: Selecionar Vídeo ────────────────────────────────
    def _pick_video(self):
        path = filedialog.askopenfilename(
            title="Selecionar Vídeo",
            filetypes=[
                ("Vídeos", "*.mp4 *.mov *.avi *.mkv *.webm"),
                ("Todos os arquivos", "*.*"),
            ]
        )
        if not path:
            return
        self.selected_video = Path(path)
        name = self.selected_video.name
        # Truncate display if too long
        display = name if len(name) <= 40 else "…" + name[-37:]
        self.lbl_video.configure(text=display, text_color=COLOR["text"])
        self._log(f"Vídeo selecionado: {name}", "muted")

    # ── Ação: Organizar ───────────────────────────────────────
    def _run(self):
        # Validações
        title = self.entry_title.get().strip()
        url   = self.entry_url.get().strip()
        store = self.combo_store.get().strip()
        badge = self.combo_badge.get().strip().upper()

        errors = []
        if not title:            errors.append("Título é obrigatório.")
        if not url:              errors.append("URL de afiliado é obrigatória.")
        if not self.selected_video: errors.append("Nenhum vídeo selecionado.")
        elif not self.selected_video.exists():
            errors.append("Arquivo de vídeo não encontrado.")

        if errors:
            for e in errors:
                self._log(e, "error")
            messagebox.showerror("Campos inválidos", "\n".join(errors))
            return

        # Feedback visual
        self.btn_organize.configure(state="disabled", text="Processando…")
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.set(0.3)
        self.update_idletasks()

        try:
            self._log(f"Iniciando organização: '{title}'")
            self.progress.set(0.5)
            self.update_idletasks()

            # 1. Copiar e renomear vídeo
            dest = organize_video(self.selected_video, title, store)
            self.progress.set(0.75)
            self.update_idletasks()

            # 2. Salvar no CSV
            row = {
                "video_path": str(dest),
                "title":      title,
                "store":      store,
                "badge":      badge,
                "url":        url,
                "status":     "done",
            }
            save_to_csv(row)
            self.progress.set(1.0)
            self.update_idletasks()

            # 3. Feedback
            self._log(f"Vídeo salvo em: {dest}", "success")
            self._log(f"Registrado no CSV: {QUEUE_CSV}", "success")
            self._add_to_history(title, store, badge, dest)
            self._reset_form()

        except Exception as e:
            self._log(f"Erro inesperado: {e}", "error")
            messagebox.showerror("Erro", str(e))

        finally:
            self.btn_organize.configure(state="normal", text="⚡  Organizar e Preparar")
            self.after(1500, lambda: self.progress.pack_forget())

    # ── Reset do formulário ───────────────────────────────────
    def _reset_form(self):
        self.entry_title.delete(0, "end")
        self.entry_url.delete(0, "end")
        self.combo_store.set("shopee")
        self.combo_badge.set("HOT")
        self.selected_video = None
        self.lbl_video.configure(text="Nenhum arquivo selecionado", text_color=COLOR["muted"])

    # ── Adiciona item no histórico ────────────────────────────
    def _add_to_history(self, title: str, store: str, badge: str, dest: Path):
        self.recent_items.append({"title": title, "store": store, "badge": badge, "dest": dest})
        self.lbl_count.configure(text=f"{len(self.recent_items)} vídeo{'s' if len(self.recent_items)>1 else ''}")

        # Remove placeholder se for o primeiro item
        if len(self.recent_items) == 1:
            self.lbl_empty_history.pack_forget()

        store_colors = {
            "shopee": COLOR["shopee"],
            "mercadolivre": COLOR["mercadolivre"],
            "outros": COLOR["outros"],
        }
        accent = store_colors.get(store, COLOR["outros"])

        item_frame = ctk.CTkFrame(
            self.history_frame,
            fg_color=COLOR["surface_high"],
            corner_radius=8,
            border_width=1, border_color=COLOR["border"],
        )
        item_frame.pack(fill="x", pady=(0, 6))

        # Accent bar
        bar = ctk.CTkFrame(item_frame, fg_color=accent, width=4, corner_radius=0)
        bar.pack(side="left", fill="y")

        content = ctk.CTkFrame(item_frame, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        top_row = ctk.CTkFrame(content, fg_color="transparent")
        top_row.pack(fill="x")

        ctk.CTkLabel(top_row, text=title,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR["text"], anchor="w").pack(side="left")

        if badge:
            ctk.CTkLabel(top_row, text=f" {badge}",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=accent).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(content,
                     text=str(dest),
                     font=ctk.CTkFont(size=10),
                     text_color=COLOR["muted"],
                     anchor="w").pack(fill="x", pady=(2, 0))


# ─── Entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    # Garante que pasta de saída existe
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    app = App()
    app.mainloop()
