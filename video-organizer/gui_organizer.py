"""
=============================================================
 Achadinhos do Momento — GUI Video Organizer  (v2 — lotes)
 Interface gráfica para organizar vídeos sem usar o terminal
 Biblioteca: CustomTkinter (pip install customtkinter)
=============================================================
Como usar:
  pip install customtkinter
  python gui_organizer.py
=============================================================
Mudanças v2:
  [LOTE-1] Seleção múltipla de vídeos (askopenfilenames)
  [LOTE-2] Subpasta por produto: organized/<loja>/<data>_<slug>/
  [LOTE-3] Vídeos numerados: clip_01.mp4, clip_02.mp4 …
  [LOTE-4] CSV recebe 1 linha por lote (video_path = pasta)
  [LOTE-5] Label mostra "X arquivos selecionados"
  [LOTE-6] Checkbox "Manter dados no formulário após organizar"
  [LOTE-7] Barra de progresso reflete cópia arquivo a arquivo
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

# ─── Tema global ──────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ─── Configurações ────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", "./organized"))
QUEUE_CSV  = Path(os.getenv("QUEUE_CSV", "./queue.csv"))

STORES = ["shopee", "mercadolivre", "outros"]
BADGES = ["HOT", "TOP", "OFERTA", "NOVO", "EXCLUSIVO", "LIMITADO", ""]

# ─── Paleta de cores ──────────────────────────────────────────
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
    "accent_muted": "#2d1f52",  # fundo sutil para destaque de keywords
}


# ─── Helper: slugify ─────────────────────────────────────────
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "_", text).strip("_")


# ─── Helper: salvar no CSV (1 linha por lote) ─────────────────
def save_to_csv(row: dict) -> None:
    """[LOTE-4] Registra uma única entrada por produto/lote."""
    fieldnames = ["video_path", "title", "store", "badge", "url", "status", "clip_count"]
    file_exists = QUEUE_CSV.exists()
    with open(QUEUE_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ─── Helper: organizar lote de vídeos ────────────────────────
def organize_batch(
    src_paths : list[Path],
    title     : str,
    store     : str,
    progress_cb,            # callable(current: int, total: int)
) -> tuple[Path, list[Path]]:
    """
    [LOTE-2] Cria subpasta  organized/<loja>/<data>_<slug>/
    [LOTE-3] Copia cada vídeo como clip_01.mp4, clip_02.mp4 …
    [LOTE-7] Chama progress_cb(i, total) após cada cópia.

    Retorna (pasta_destino, lista_de_arquivos_copiados).
    """
    today     = datetime.now().strftime("%Y-%m-%d")
    slug      = slugify(title)
    store_dir = OUTPUT_DIR / store
    # [LOTE-2] Subpasta específica do produto
    project_dir = store_dir / f"{today}_{slug}"
    project_dir.mkdir(parents=True, exist_ok=True)

    total   = len(src_paths)
    copied  = []

    for i, src in enumerate(src_paths, start=1):
        # [LOTE-3] Numera com padding: clip_01, clip_02 …
        pad    = str(i).zfill(2)
        dest   = project_dir / f"clip_{pad}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        copied.append(dest)
        # [LOTE-7] Atualiza progresso após cada arquivo copiado
        progress_cb(i, total)

    return project_dir, copied


# ══════════════════════════════════════════════════════════════
# JANELA PRINCIPAL
# ══════════════════════════════════════════════════════════════

class App(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("Achadinhos — Video Organizer v2")
        self.geometry("640x820")
        self.minsize(600, 720)
        self.configure(fg_color=COLOR["bg"])
        self.resizable(True, True)

        # ── Estado ────────────────────────────────────────────
        # [LOTE-1] Lista de caminhos em vez de um único Path
        self.selected_videos: list[Path] = []
        self.recent_items:    list[dict] = []

        # ── Construção da UI ──────────────────────────────────
        self._build_header()
        self._build_form()
        self._build_options()   # [LOTE-6] checkbox + configs
        self._build_actions()
        self._build_status()
        self._build_history()

        self._log("Pronto. Selecione um ou mais vídeos para começar.", "muted")

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

        # Badge de versão
        ctk.CTkLabel(inner, text="v2 · lotes",
                     font=ctk.CTkFont(size=10),
                     text_color=COLOR["primary"]).pack(side="right", padx=(0, 8))

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

        # ── Seleção de vídeos ─────────────────────────────────
        self._label(wrapper, "Arquivos de Vídeo *  (selecione um ou mais)")
        video_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        video_row.pack(fill="x", pady=(4, 0))

        # [LOTE-5] Label dinâmico
        self.lbl_video = ctk.CTkLabel(
            video_row,
            text="Nenhum arquivo selecionado",
            text_color=COLOR["muted"],
            font=ctk.CTkFont(size=12),
            anchor="w",
            fg_color=COLOR["surface_high"],
            corner_radius=8,
        )
        self.lbl_video.pack(side="left", fill="x", expand=True,
                            ipady=8, ipadx=10, padx=(0, 8))

        ctk.CTkButton(
            video_row, text="📂  Selecionar",
            width=130, height=38,
            fg_color=COLOR["surface_high"],
            hover_color=COLOR["border"],
            border_color=COLOR["border"], border_width=1,
            text_color=COLOR["text"],
            font=ctk.CTkFont(size=13),
            command=self._pick_videos,   # [LOTE-1]
        ).pack(side="right")

        # Preview compacto da lista de arquivos
        self.lbl_files_detail = ctk.CTkLabel(
            wrapper, text="",
            text_color=COLOR["muted"],
            font=ctk.CTkFont(size=10),
            anchor="w",
            wraplength=560,
            justify="left",
        )
        self.lbl_files_detail.pack(fill="x", pady=(4, 0))

    # ── Opções extras (checkbox) ───────────────────────────────
    def _build_options(self):
        """[LOTE-6] Área de opções com checkbox de manter dados."""
        frame = ctk.CTkFrame(self,
                             fg_color=COLOR["surface"],
                             corner_radius=10,
                             border_width=1,
                             border_color=COLOR["border"])
        frame.pack(fill="x", padx=20, pady=(14, 0))

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        # [LOTE-6] Variável do checkbox
        self.keep_data_var = ctk.BooleanVar(value=False)

        self.chk_keep_data = ctk.CTkCheckBox(
            inner,
            text="Manter dados no formulário após organizar",
            variable=self.keep_data_var,
            fg_color=COLOR["primary"],
            hover_color=COLOR["primary_hover"],
            border_color=COLOR["border"],
            text_color=COLOR["text"],
            font=ctk.CTkFont(size=12),
            checkmark_color="#ffffff",
        )
        self.chk_keep_data.pack(side="left")

        ctk.CTkLabel(
            inner,
            text="Útil ao organizar vários lotes do mesmo produto",
            font=ctk.CTkFont(size=10),
            text_color=COLOR["muted"],
        ).pack(side="right")

    # ── Botão principal ────────────────────────────────────────
    def _build_actions(self):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=20, pady=14)

        self.btn_organize = ctk.CTkButton(
            frame,
            text="⚡  Organizar Lote",
            height=52,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR["primary"],
            hover_color=COLOR["primary_hover"],
            corner_radius=12,
            command=self._run,
        )
        self.btn_organize.pack(fill="x")

        # Legenda do progresso
        self.lbl_progress = ctk.CTkLabel(
            frame, text="",
            font=ctk.CTkFont(size=10),
            text_color=COLOR["muted"],
        )
        self.lbl_progress.pack(pady=(4, 0))

        # Barra de progresso
        self.progress = ctk.CTkProgressBar(
            frame, height=4, corner_radius=99,
            fg_color=COLOR["surface_high"],
            progress_color=COLOR["primary"],
        )
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(4, 0))
        self.progress.pack_forget()

    # ── Status ─────────────────────────────────────────────────
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
            font=ctk.CTkFont(
                family="Courier New" if sys.platform == "win32" else "Menlo",
                size=12
            ),
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
        ctk.CTkLabel(header, text="Lotes Organizados Nesta Sessão",
                     font=ctk.CTkFont(size=11),
                     text_color=COLOR["muted"]).pack(side="left")
        self.lbl_count = ctk.CTkLabel(header, text="0 lotes",
                                       font=ctk.CTkFont(size=11),
                                       text_color=COLOR["muted"])
        self.lbl_count.pack(side="right")

        self.history_frame = ctk.CTkScrollableFrame(
            frame, fg_color="transparent",
            scrollbar_button_color=COLOR["border"])
        self.history_frame.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        self.lbl_empty_history = ctk.CTkLabel(
            self.history_frame,
            text="Nenhum lote organizado ainda.",
            text_color=COLOR["muted"],
            font=ctk.CTkFont(size=12),
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
        prefix_map = {
            "error":   "✗ ",
            "success": "✓ ",
            "warning": "⚠ ",
            "muted":   "  ",
        }
        prefix = prefix_map.get(level, "→ ")
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {prefix}{msg}\n"

        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line)
        self.txt_log.configure(state="disabled")
        self.txt_log.see("end")

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    # ── Ação: Selecionar Vídeos ───────────────────────────────
    def _pick_videos(self):
        """[LOTE-1] Seleção múltipla com askopenfilenames."""
        paths = filedialog.askopenfilenames(
            title="Selecionar Vídeo(s) do Produto",
            filetypes=[
                ("Vídeos", "*.mp4 *.mov *.avi *.mkv *.webm"),
                ("Todos os arquivos", "*.*"),
            ]
        )
        if not paths:
            return

        self.selected_videos = [Path(p) for p in paths]
        count = len(self.selected_videos)

        # [LOTE-5] Label dinâmico
        if count == 1:
            name    = self.selected_videos[0].name
            display = name if len(name) <= 42 else "…" + name[-39:]
            self.lbl_video.configure(text=display, text_color=COLOR["text"])
        else:
            self.lbl_video.configure(
                text=f"📹  {count} arquivos selecionados",
                text_color=COLOR["primary"],
            )

        # Preview compacto dos nomes
        names = [p.name for p in self.selected_videos]
        preview = "  •  ".join(names[:4])
        if count > 4:
            preview += f"  …e mais {count - 4}"
        self.lbl_files_detail.configure(text=preview)

        self._log(f"{count} arquivo(s) selecionado(s): {', '.join(names[:3])}"
                  + (f" …+{count-3}" if count > 3 else ""), "muted")

    # ── Ação: Organizar Lote ───────────────────────────────────
    def _run(self):
        title = self.entry_title.get().strip()
        url   = self.entry_url.get().strip()
        store = self.combo_store.get().strip()
        badge = self.combo_badge.get().strip().upper()

        # Validações
        errors = []
        if not title:
            errors.append("Título é obrigatório.")
        if not url:
            errors.append("URL de afiliado é obrigatória.")
        if not self.selected_videos:
            errors.append("Nenhum vídeo selecionado.")
        else:
            missing = [v.name for v in self.selected_videos if not v.exists()]
            if missing:
                errors.append(f"Arquivo(s) não encontrado(s): {', '.join(missing)}")

        if errors:
            for e in errors:
                self._log(e, "error")
            messagebox.showerror("Campos inválidos", "\n".join(errors))
            return

        total = len(self.selected_videos)

        # Feedback visual inicial
        self.btn_organize.configure(state="disabled",
                                    text=f"Copiando 0/{total}…")
        self.lbl_progress.configure(text=f"Preparando {total} arquivo(s)…")
        self.progress.pack(fill="x", pady=(4, 0))
        self.progress.set(0)
        self.update_idletasks()

        try:
            self._log(f"Iniciando lote '{title}' — {total} clipe(s)")

            # [LOTE-7] Callback de progresso chamado a cada arquivo copiado
            def _on_progress(current: int, total_: int):
                ratio = current / total_
                self.progress.set(ratio)
                self.btn_organize.configure(
                    text=f"Copiando {current}/{total_}…"
                )
                self.lbl_progress.configure(
                    text=f"Copiado: clip_{str(current).zfill(2)} "
                         f"({current}/{total_})"
                )
                self.update_idletasks()

            # [LOTE-2/3] Organizar e copiar todos os arquivos
            project_dir, copied = organize_batch(
                src_paths   = self.selected_videos,
                title       = title,
                store       = store,
                progress_cb = _on_progress,
            )

            # [LOTE-4] Uma única linha no CSV apontando para a pasta
            row = {
                "video_path" : str(project_dir),
                "title"      : title,
                "store"      : store,
                "badge"      : badge,
                "url"        : url,
                "status"     : "done",
                "clip_count" : total,
            }
            save_to_csv(row)

            self.progress.set(1.0)
            self.update_idletasks()

            self._log(f"Pasta criada: {project_dir}", "success")
            self._log(f"{total} clipe(s) copiado(s) com sucesso", "success")
            self._log(f"Registrado no CSV: {QUEUE_CSV}", "success")

            self._add_to_history(title, store, badge, project_dir, total)
            self._reset_form()

        except Exception as e:
            self._log(f"Erro inesperado: {e}", "error")
            messagebox.showerror("Erro", str(e))

        finally:
            self.btn_organize.configure(state="normal",
                                        text="⚡  Organizar Lote")
            self.lbl_progress.configure(text="")
            self.after(1800, lambda: self.progress.pack_forget())

    # ── Reset do formulário ───────────────────────────────────
    def _reset_form(self):
        """
        [LOTE-6] Reseta sempre a seleção de arquivos.
        Reseta título/URL/badge apenas se o checkbox NÃO estiver marcado.
        """
        # Seleção de arquivos: sempre limpa (cada lote = novos clipes)
        self.selected_videos = []
        self.lbl_video.configure(
            text="Nenhum arquivo selecionado",
            text_color=COLOR["muted"],
        )
        self.lbl_files_detail.configure(text="")

        if not self.keep_data_var.get():
            # Limpa o formulário completo
            self.entry_title.delete(0, "end")
            self.entry_url.delete(0, "end")
            self.combo_store.set("shopee")
            self.combo_badge.set("HOT")

    # ── Adiciona item no histórico ────────────────────────────
    def _add_to_history(
        self,
        title     : str,
        store     : str,
        badge     : str,
        dest      : Path,
        clip_count: int,
    ):
        self.recent_items.append({
            "title": title, "store": store,
            "badge": badge, "dest": dest,
            "clips": clip_count,
        })
        n = len(self.recent_items)
        self.lbl_count.configure(
            text=f"{n} lote{'s' if n > 1 else ''}"
        )

        if n == 1:
            self.lbl_empty_history.pack_forget()

        store_colors = {
            "shopee":       COLOR["shopee"],
            "mercadolivre": COLOR["mercadolivre"],
            "outros":       COLOR["outros"],
        }
        accent = store_colors.get(store, COLOR["outros"])

        item_frame = ctk.CTkFrame(
            self.history_frame,
            fg_color=COLOR["surface_high"],
            corner_radius=8,
            border_width=1,
            border_color=COLOR["border"],
        )
        item_frame.pack(fill="x", pady=(0, 6))

        # Barra de acento lateral
        ctk.CTkFrame(item_frame, fg_color=accent,
                     width=4, corner_radius=0).pack(side="left", fill="y")

        content = ctk.CTkFrame(item_frame, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Linha superior: título + badge + contagem de clips
        top_row = ctk.CTkFrame(content, fg_color="transparent")
        top_row.pack(fill="x")

        ctk.CTkLabel(
            top_row, text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR["text"], anchor="w",
        ).pack(side="left")

        if badge:
            ctk.CTkLabel(
                top_row, text=f" {badge}",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=accent,
            ).pack(side="left", padx=(6, 0))

        # [LOTE] Pill com contagem de clipes
        ctk.CTkLabel(
            top_row,
            text=f"  📹 {clip_count} clipe{'s' if clip_count > 1 else ''}",
            font=ctk.CTkFont(size=10),
            text_color=COLOR["muted"],
        ).pack(side="left", padx=(8, 0))

        # Caminho da pasta de destino
        ctk.CTkLabel(
            content,
            text=str(dest),
            font=ctk.CTkFont(size=10),
            text_color=COLOR["muted"],
            anchor="w",
        ).pack(fill="x", pady=(2, 0))


# ─── Entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = App()
    app.mainloop()
