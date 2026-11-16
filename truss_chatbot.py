"""
Tkinter Chatbot Interface for Truss Analysis
Desktop GUI for interacting with the LLM-powered truss analysis tools
Styled with CustomTkinter for a modern light UI
"""

import customtkinter as ctk
import tkinter as tk
import threading
import subprocess
import os
import sys
import webbrowser
from io import BytesIO

# Image handling
try:
    from PIL import Image, ImageTk, ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Warning: Pillow not installed. Image paste disabled. Run: pip install Pillow")

# Import tools directly for quick actions
import truss_tools

# Image file path for pasted truss images
PASTED_IMAGE_FILE = os.path.join(os.path.dirname(__file__), "truss_image.png")

# Global agent instance (for natural language queries)
agent = None

# ── Appearance ──
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ── Design constants (warm light theme — easy on the eyes) ──
COLOR_BG_DARK = "#f5f0e8"       # warm cream for main surfaces (chat, canvas)
COLOR_SURFACE = "#ebe5da"       # slightly darker warm tone for sidebar/cards
COLOR_SURFACE_2 = "#dfd8cb"     # hover / pressed state
COLOR_BORDER = "#c8bfb0"        # soft warm border
COLOR_TEXT = "#2d2a26"           # dark warm brown, not pure black
COLOR_TEXT_DIM = "#7a746b"       # muted warm gray
COLOR_ACCENT = "#3b6fc2"        # calmer blue (less saturated than pure blue)
COLOR_SUCCESS = "#3a8a5c"       # muted forest green
COLOR_WARNING = "#b8860b"       # dark goldenrod
COLOR_DANGER = "#c0392b"        # muted red

CORNER_R = 8
PAD_OUTER = 16
PAD_SECTION = 12
PAD_WIDGET = 8


class TrussChatbot(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Truss Analysis Chatbot")
        self.geometry("1300x780")
        self.minsize(960, 600)

        # Initialize agent in background
        self.agent = None
        self.is_processing = False
        self._sap_timeout_id = None

        # Image panel variables
        self.current_image = None
        self.photo_image = None
        # 3D viewer variables
        self._current_3d_html = None
        self._viewer_process = None
        self.plot_image = None
        self.plot_photo = None
        # Canvas resize debounce jobs
        self._img_resize_job = None
        self._plot_resize_job = None
        # Compare-solvers: stored data so direction switch redraws instantly
        self._cmp_sap    = {}
        self._cmp_oss    = {}
        self._cmp_sap_ax = {}
        self._cmp_oss_ax = {}
        self._disp_dir_var = tk.StringVar(value="|U|")

        # ── Fonts ──
        self.font_heading = ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        self.font_body = ctk.CTkFont(family="Segoe UI", size=15)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=13)
        self.font_mono = ctk.CTkFont(family="Consolas", size=13)

        self._build_ui()
        self.init_agent_async()

        # Set global app reference for tools to display plots
        truss_tools.set_chatbot_app(self)

    def _build_ui(self):
        # ── Root grid: sidebar | main ──
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()

        # Global bindings
        self.bind("<Control-v>", self.on_paste_image)

        # Welcome messages
        self._append_message("System", "Welcome to Truss Analysis Chatbot!", "system")
        self._append_message("System", "Quick Actions are on the left. Type messages below to chat with AI.", "system")
        if PIL_AVAILABLE:
            self._append_message("System", "Ctrl+V to paste truss images from clipboard.", "system")

    # ──────────────────────────────────────────────────────────
    # Sidebar
    # ──────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLOR_SURFACE)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(20, weight=1)  # push items up
        sidebar.grid_propagate(False)

        # Logo / title
        ctk.CTkLabel(
            sidebar, text="Truss Analysis",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=(24, 2), sticky="w")

        ctk.CTkLabel(
            sidebar, text="Chatbot",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color=COLOR_TEXT_DIM,
        ).grid(row=1, column=0, padx=20, pady=(0, 16), sticky="w")

        # Divider
        ctk.CTkFrame(sidebar, height=1, fg_color=COLOR_BORDER).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

        # Section label
        ctk.CTkLabel(
            sidebar, text="QUICK ACTIONS",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLOR_TEXT_DIM,
        ).grid(row=3, column=0, padx=20, pady=(0, 6), sticky="w")

        # Action buttons
        actions = [
            ("Check AutoCAD",  self.action_check_autocad, COLOR_ACCENT),
            ("Get Truss",      self.action_get_truss,     COLOR_ACCENT),
            ("Run Analysis",   self.action_run_analysis,  COLOR_WARNING),
            ("Draw Truss",     self.action_draw_truss,    COLOR_ACCENT),
            ("3D Solids",      self.action_3d_solids,     "#7b52a0"),
            ("Query Data",     self.action_query_data,    None),
            ("Assess EC3",     self.action_assess_ec3,    COLOR_DANGER),
            ("Optimize",       self.action_optimize,      COLOR_SUCCESS),
            ("SAP2000",        self.action_sap2000,       "#1a7a8a"),
            ("Compare Solvers", self.action_compare_solvers, "#c06020"),
        ]

        for i, (text, cmd, color) in enumerate(actions):
            btn = ctk.CTkButton(
                sidebar, text=text, command=cmd,
                fg_color="transparent",
                text_color=COLOR_TEXT,
                hover_color=COLOR_SURFACE_2,
                anchor="w", height=36,
                border_width=1,
                border_color=color or COLOR_BORDER,
                corner_radius=CORNER_R,
            )
            btn.grid(row=4 + i, column=0, padx=12, pady=3, sticky="ew")

        # Spacer pushes status + clear to bottom
        # row 20 has weight=1

        # Force Unlock — releases processing lock if a tool hangs
        ctk.CTkButton(
            sidebar, text="Force Unlock", command=self._force_unlock,
            fg_color="transparent", text_color=COLOR_DANGER,
            hover_color=COLOR_SURFACE_2, anchor="w", height=28,
            border_width=1, border_color=COLOR_DANGER,
            corner_radius=CORNER_R,
        ).grid(row=21, column=0, padx=12, pady=(0, 2), sticky="ew")

        # Clear chat at bottom
        ctk.CTkButton(
            sidebar, text="Clear Chat", command=self.clear_chat,
            fg_color="transparent", text_color=COLOR_TEXT_DIM,
            hover_color=COLOR_SURFACE_2, anchor="w", height=32,
            corner_radius=CORNER_R,
        ).grid(row=22, column=0, padx=12, pady=(0, 4), sticky="ew")

        # Status label at very bottom
        self.status_var = tk.StringVar(value="Initializing...")
        self.status_label = ctk.CTkLabel(
            sidebar, textvariable=self.status_var,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLOR_TEXT_DIM,
        )
        self.status_label.grid(row=23, column=0, padx=20, pady=(0, 16), sticky="w")

    # ──────────────────────────────────────────────────────────
    # Main content area
    # ──────────────────────────────────────────────────────────
    def _build_main_area(self):
        # ── Horizontal PanedWindow: chat | image+plot panels ──
        self._paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=6, sashrelief=tk.FLAT,
            sashcursor="sb_h_double_arrow",
            bg=COLOR_BORDER,
        )
        self._paned.grid(row=0, column=1, sticky="nsew")

        # ── Left pane: chat ──
        chat_col = ctk.CTkFrame(self._paned, fg_color="transparent")
        chat_col.grid_rowconfigure(0, weight=1)
        chat_col.grid_columnconfigure(0, weight=1)
        self._paned.add(chat_col, stretch="always", minsize=380)

        self._build_chat(chat_col)
        self._build_input_bar(chat_col)

        # ── Right pane: image + plot panels ──
        img_col = ctk.CTkFrame(self._paned, fg_color="transparent")
        img_col.grid_rowconfigure(0, weight=1)
        img_col.grid_rowconfigure(1, weight=1)
        img_col.grid_columnconfigure(0, weight=1)
        self._paned.add(img_col, stretch="always", minsize=200)

        self._build_truss_image_panel(img_col)
        self._build_plot_panel(img_col)

        # Debounce job IDs for canvas resize events
        self._img_resize_job = None
        self._plot_resize_job = None

    # ──────────────────────────────────────────────────────────
    # Chat display
    # ──────────────────────────────────────────────────────────
    def _build_chat(self, parent):
        # CTkTextbox for the chat — styled, scrollable, dark
        self.chat_display = ctk.CTkTextbox(
            parent,
            font=self.font_mono,
            fg_color=COLOR_BG_DARK,
            text_color=COLOR_TEXT,
            corner_radius=CORNER_R,
            border_width=1,
            border_color=COLOR_BORDER,
            state="disabled",
            wrap="word",
        )
        self.chat_display.grid(row=0, column=0, sticky="nsew", pady=(0, PAD_WIDGET))

        # Configure text tags for colored sender labels (no font — CTkTextbox forbids it)
        self.chat_display.tag_config("user",   foreground="#3b6fc2")
        self.chat_display.tag_config("bot",    foreground="#3a8a5c")
        self.chat_display.tag_config("system", foreground="#a09888")
        self.chat_display.tag_config("tool",   foreground="#b8860b")

    # ──────────────────────────────────────────────────────────
    # Input bar
    # ──────────────────────────────────────────────────────────
    def _build_input_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        self.input_entry = ctk.CTkEntry(
            bar,
            font=self.font_body,
            placeholder_text="Type a message...",
            height=40,
            corner_radius=CORNER_R,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(0, PAD_WIDGET))
        self.input_entry.bind("<Return>", self.on_send)

        self.send_btn = ctk.CTkButton(
            bar, text="Send", command=self.on_send,
            width=80, height=40,
            corner_radius=CORNER_R,
            fg_color=COLOR_ACCENT,
        )
        self.send_btn.grid(row=0, column=1)

    # ──────────────────────────────────────────────────────────
    # Truss image panel
    # ──────────────────────────────────────────────────────────
    def _build_truss_image_panel(self, parent):
        frame = ctk.CTkFrame(parent, corner_radius=CORNER_R, fg_color=COLOR_SURFACE,
                             border_width=1, border_color=COLOR_BORDER)
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, PAD_WIDGET))
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD_SECTION, pady=(PAD_SECTION, 4))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Truss Image", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.image_info_var = tk.StringVar(value="No image")
        ctk.CTkLabel(header, textvariable=self.image_info_var, font=self.font_small,
                     text_color=COLOR_TEXT_DIM).grid(row=0, column=1, sticky="e")

        # Canvas
        self.image_canvas = tk.Canvas(
            frame, bg=COLOR_BG_DARK,
            highlightthickness=0, height=160,
        )
        self.image_canvas.grid(row=1, column=0, sticky="nsew", padx=PAD_SECTION, pady=4)
        self.image_canvas.bind("<Configure>", self._on_image_canvas_resize)

        self.image_placeholder_id = self.image_canvas.create_text(
            150, 80, text="Ctrl+V to paste", fill="#b0b0b0",
            font=("Segoe UI", 10), justify=tk.CENTER,
        )

        # Buttons
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=PAD_SECTION, pady=(4, PAD_SECTION))

        self.paste_btn = ctk.CTkButton(
            btn_row, text="Paste", command=self.paste_image_from_clipboard,
            width=70, height=28, corner_radius=6,
            fg_color="transparent", border_width=1, border_color=COLOR_ACCENT,
            text_color=COLOR_ACCENT, hover_color=COLOR_SURFACE_2,
        )
        self.paste_btn.pack(side="left", padx=(0, 4))

        self.clear_img_btn = ctk.CTkButton(
            btn_row, text="Clear", command=self.clear_image,
            width=70, height=28, corner_radius=6,
            fg_color="transparent", border_width=1, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_DIM, hover_color=COLOR_SURFACE_2,
        )
        self.clear_img_btn.pack(side="left", padx=(0, 4))

        self.open_viewer_btn = ctk.CTkButton(
            btn_row, text="3D Viewer", command=self.open_3d_viewer,
            width=80, height=28, corner_radius=6,
            fg_color="transparent", border_width=1, border_color="#3a8a5c",
            text_color="#3a8a5c", hover_color=COLOR_SURFACE_2,
        )
        self.open_viewer_btn.pack(side="left")

        if not PIL_AVAILABLE:
            self.paste_btn.configure(state="disabled")
            self.image_info_var.set("Install Pillow")

    # ──────────────────────────────────────────────────────────
    # Plot panel
    # ──────────────────────────────────────────────────────────
    def _build_plot_panel(self, parent):
        frame = ctk.CTkFrame(parent, corner_radius=CORNER_R, fg_color=COLOR_SURFACE,
                             border_width=1, border_color=COLOR_BORDER)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD_SECTION, pady=(PAD_SECTION, 4))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Analysis Plots", font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.plot_info_var = tk.StringVar(value="No plot")
        ctk.CTkLabel(header, textvariable=self.plot_info_var, font=self.font_small,
                     text_color=COLOR_TEXT_DIM).grid(row=0, column=1, sticky="e")

        # Canvas
        self.plot_canvas = tk.Canvas(
            frame, bg=COLOR_BG_DARK,
            highlightthickness=0, height=160,
        )
        self.plot_canvas.grid(row=1, column=0, sticky="nsew", padx=PAD_SECTION, pady=4)
        self.plot_canvas.bind("<Configure>", self._on_plot_canvas_resize)

        self.plot_placeholder_id = self.plot_canvas.create_text(
            150, 80, text="Plots appear here", fill="#b0b0b0",
            font=("Segoe UI", 10), justify=tk.CENTER,
        )

        # Buttons
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=PAD_SECTION, pady=(4, PAD_SECTION))

        self.clear_plot_btn = ctk.CTkButton(
            btn_row, text="Clear", command=self.clear_plot,
            width=70, height=28, corner_radius=6,
            fg_color="transparent", border_width=1, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_DIM, hover_color=COLOR_SURFACE_2,
        )
        self.clear_plot_btn.pack(side="left", padx=(0, 4))

        self.save_plot_btn = ctk.CTkButton(
            btn_row, text="Save", command=self.save_plot,
            width=70, height=28, corner_radius=6,
            fg_color="transparent", border_width=1, border_color=COLOR_SUCCESS,
            text_color=COLOR_SUCCESS, hover_color=COLOR_SURFACE_2,
        )
        self.save_plot_btn.pack(side="left")

        ctk.CTkLabel(btn_row, text="  Disp:", font=self.font_small,
                     text_color=COLOR_TEXT_DIM).pack(side="left", padx=(10, 2))
        self._disp_seg = ctk.CTkSegmentedButton(
            btn_row, values=["|U|", "X", "Y", "Z"],
            variable=self._disp_dir_var,
            command=self._on_disp_dir_change,
            height=26, font=self.font_small,
        )
        self._disp_seg.pack(side="left")

    # ════════════════════════════════════════════════════════
    # Compare-plot helpers
    # ════════════════════════════════════════════════════════

    def _on_disp_dir_change(self, direction):
        if not self._cmp_sap:
            return
        import threading
        threading.Thread(
            target=lambda: self._draw_compare_plot(
                self._cmp_sap, self._cmp_oss,
                self._cmp_sap_ax, self._cmp_oss_ax,
                direction),
            daemon=True).start()

    def _draw_compare_plot(self, sap, oss, sap_ax, oss_ax, direction="|U|"):
        import math, os
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np

        DIR_IDX   = {"|U|": None, "X": 0, "Y": 1, "Z": 2}
        DIR_LABEL = {"|U|": "|U| (mm)", "X": "UX (mm)", "Y": "UY (mm)", "Z": "UZ (mm)"}
        DIR_TITLE = {
            "|U|": "Nodal Displacement Magnitude",
            "X":   "Nodal Displacement — X direction",
            "Y":   "Nodal Displacement — Y direction",
            "Z":   "Nodal Displacement — Z direction",
        }

        common = sorted(set(sap) & set(oss))
        if not common:
            return

        FREE_TOL = 1e-6
        sap_mag_all = [math.sqrt(sum(v**2 for v in sap[j])) for j in common]
        free_idx  = [i for i, m in enumerate(sap_mag_all) if m > FREE_TOL]
        common_f  = [common[i] for i in free_idx]

        idx = DIR_IDX[direction]
        if idx is None:
            sap_f = [math.sqrt(sum(v**2 for v in sap[j])) for j in common_f]
            oss_f = [math.sqrt(sum(v**2 for v in oss[j])) for j in common_f]
        else:
            sap_f = [sap[j][idx] for j in common_f]
            oss_f = [oss[j][idx] for j in common_f]

        common_e = sorted(set(sap_ax) & set(oss_ax))
        sap_axf  = [sap_ax[e] for e in common_e]
        oss_axf  = [oss_ax[e] for e in common_e]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), facecolor="#f5f0e8")
        fig.subplots_adjust(hspace=0.45)
        x  = np.arange(len(common_f))
        xe = np.arange(len(common_e))
        w  = 0.4

        for ax in (ax1, ax2):
            ax.set_facecolor("#ffffff")
            ax.tick_params(colors="#2d2a26", labelsize=14)
            ax.xaxis.label.set_color("#2d2a26")
            ax.yaxis.label.set_color("#2d2a26")
            ax.title.set_color("#2d2a26")
            for sp in ax.spines.values():
                sp.set_color("#c8bfb0")

        ax1.bar(x - w/2, sap_f, w, label="SAP2000", color="#1565c0", edgecolor="#c8bfb0")
        ax1.bar(x + w/2, oss_f, w, label="OpenSees", color="#e65100", edgecolor="#c8bfb0")
        ax1.set_xticks(x)
        ax1.set_xticklabels([str(j) for j in common_f], rotation=90, fontsize=14)
        ax1.set_xlabel("Joint ID", fontsize=16)
        ax1.set_ylabel(DIR_LABEL[direction], fontsize=16)
        ax1.set_title(f"{DIR_TITLE[direction]} — SAP2000 vs OpenSees  [Live Load]", fontsize=18)
        ax1.legend(facecolor="#ebe5da", edgecolor="#c8bfb0", labelcolor="#2d2a26", fontsize=15)
        if direction != "|U|":
            ax1.axhline(y=0, color="#555555", linewidth=0.8)

        ax2.bar(xe - w/2, sap_axf, w, label="SAP2000", color="#1565c0", edgecolor="#c8bfb0")
        ax2.bar(xe + w/2, oss_axf, w, label="OpenSees", color="#e65100", edgecolor="#c8bfb0")
        ax2.set_xticks(xe)
        ax2.set_xticklabels([str(e) for e in common_e], rotation=90, fontsize=12)
        ax2.set_xlabel("Element ID", fontsize=16)
        ax2.set_ylabel("Axial Force (kN)", fontsize=16)
        ax2.set_title("Member Axial Forces — SAP2000 vs OpenSees  [Live Load]", fontsize=18)
        ax2.axhline(y=0, color="#555555", linewidth=0.8)
        ax2.legend(facecolor="#ebe5da", edgecolor="#c8bfb0", labelcolor="#2d2a26", fontsize=15)

        plot_path = os.path.join(os.path.dirname(__file__), "compare_plot.png")
        fig.savefig(plot_path, dpi=130, bbox_inches="tight",
                    facecolor="#f5f0e8", edgecolor="none")
        plt.close(fig)

        title = f"SAP2000 vs OpenSees — {direction}"
        self.after(0, lambda: self.display_plot(plot_path, title))

    # ════════════════════════════════════════════════════════
    # Chat helpers
    # ════════════════════════════════════════════════════════

    def _append_message(self, sender, message, tag="user"):
        """Add a message to the chat display."""
        self.chat_display.configure(state="normal")
        if sender:
            self.chat_display.insert("end", f"\n{sender}: ", tag)
            self.chat_display.insert("end", f"{message}\n")
        else:
            self.chat_display.insert("end", f"{message}\n")
        self.chat_display.see("end")
        self.chat_display.configure(state="disabled")

    # Keep old name as alias for compatibility with truss_tools
    def add_message(self, sender, message, tag="user"):
        self._append_message(sender, message, tag)

    def set_processing(self, processing, status_text="Processing..."):
        self.is_processing = processing
        if processing:
            self.status_var.set(status_text)
            self.send_btn.configure(state="disabled")
        else:
            self.status_var.set("Ready")
            self.send_btn.configure(state="normal")

    # ════════════════════════════════════════════════════════
    # Canvas resize handlers (debounced — 80 ms)
    # ════════════════════════════════════════════════════════

    def _on_image_canvas_resize(self, event=None):
        if self._img_resize_job:
            self.after_cancel(self._img_resize_job)
        self._img_resize_job = self.after(80, self._redraw_image_canvas)

    def _redraw_image_canvas(self):
        self._img_resize_job = None
        if self.current_image is not None:
            self.display_image(self.current_image)

    def _on_plot_canvas_resize(self, event=None):
        if self._plot_resize_job:
            self.after_cancel(self._plot_resize_job)
        self._plot_resize_job = self.after(80, self._redraw_plot_canvas)

    def _redraw_plot_canvas(self):
        self._plot_resize_job = None
        if self.plot_image is not None:
            self.display_plot(self.plot_image, self.plot_info_var.get())

    # ════════════════════════════════════════════════════════
    # Image handling
    # ════════════════════════════════════════════════════════

    def on_paste_image(self, event=None):
        if self.focus_get() == self.input_entry:
            return
        self.paste_image_from_clipboard()

    def paste_image_from_clipboard(self):
        if not PIL_AVAILABLE:
            self._append_message("System", "Pillow not installed. Run: pip install Pillow", "system")
            return
        try:
            image = ImageGrab.grabclipboard()
            if image is None:
                self._append_message("System", "No image in clipboard. Use Snipping Tool (Win+Shift+S) to capture.", "system")
                return
            if not isinstance(image, Image.Image):
                self._append_message("System", "Clipboard doesn't contain an image.", "system")
                return
            self.current_image = image
            image.save(PASTED_IMAGE_FILE, "PNG")
            self.display_image(image)
            self.image_info_var.set(f"{image.width}x{image.height}")
            self._append_message("System", f"Image pasted ({image.width}x{image.height}). Saved to truss_image.png", "system")
        except Exception as e:
            self._append_message("System", f"Failed to paste image: {str(e)}", "system")

    def display_image(self, image):
        self.image_canvas.update_idletasks()
        cw = max(self.image_canvas.winfo_width(), 50)
        ch = max(self.image_canvas.winfo_height(), 50)
        scale = min(cw / image.width, ch / image.height, 1.0)
        nw, nh = int(image.width * scale), int(image.height * scale)
        resized = image.resize((nw, nh), Image.Resampling.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(resized)
        self.image_canvas.delete("all")
        self.image_canvas.create_image(cw // 2, ch // 2, image=self.photo_image, anchor=tk.CENTER)

    def clear_image(self):
        self.current_image = None
        self.photo_image = None
        self.image_canvas.delete("all")
        self.image_placeholder_id = self.image_canvas.create_text(
            150, 80, text="Ctrl+V to paste", fill="#b0b0b0",
            font=("Segoe UI", 10), justify=tk.CENTER,
        )
        self.image_info_var.set("No image")
        self._append_message("System", "Truss image cleared.", "system")
        if os.path.exists(PASTED_IMAGE_FILE):
            try:
                os.remove(PASTED_IMAGE_FILE)
            except Exception:
                pass

    # ════════════════════════════════════════════════════════
    # 3D Truss Viewer
    # ════════════════════════════════════════════════════════

    def refresh_3d_viewer(self):
        """Rebuild 3D viewer HTML file in background."""
        truss_file = os.path.join(os.path.dirname(__file__), "truss.txt")
        if not os.path.exists(truss_file):
            return

        def build():
            try:
                import truss_viewer_3d as tv
                html, elements_data = tv.build_viewer_html()
                out_path = os.path.join(os.path.dirname(__file__), "_truss_3d_view.html")
                with open(out_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                self._current_3d_html = out_path
            except Exception as exc:
                msg = f"3D viewer error: {exc}"
                self.after(0, lambda m=msg: self._append_message("System", m, "system"))

        threading.Thread(target=build, daemon=True).start()

    def open_3d_viewer(self):
        """Open the interactive 3D viewer — offer Original/Optimized choice if available."""
        truss_file = os.path.join(os.path.dirname(__file__), "truss.txt")
        if not os.path.exists(truss_file):
            self._append_message("System", "No truss.txt found. Extract truss first.", "system")
            return

        optimized_file = os.path.join(os.path.dirname(__file__), "truss_optimized.txt")
        if os.path.exists(optimized_file):
            # Show choice dialog
            choice_win = ctk.CTkToplevel(self)
            choice_win.title("3D Viewer")
            choice_win.geometry("320x160")
            choice_win.resizable(False, False)
            choice_win.transient(self)
            choice_win.grab_set()

            ctk.CTkLabel(choice_win, text="Which truss do you want to view?",
                         font=ctk.CTkFont(size=14)).pack(pady=(20, 15))

            btn_frame = ctk.CTkFrame(choice_win, fg_color="transparent")
            btn_frame.pack(pady=5)

            def view_version(version):
                choice_win.destroy()
                self._launch_3d_viewer(version)

            ctk.CTkButton(btn_frame, text="Original", width=120,
                          command=lambda: view_version("original")).pack(side="left", padx=8)
            ctk.CTkButton(btn_frame, text="Optimized", width=120,
                          command=lambda: view_version("optimized")).pack(side="left", padx=8)
        else:
            self._launch_3d_viewer("original")

    def _launch_3d_viewer(self, version="original"):
        """Build HTML for the chosen version and open the PyQt5 viewer."""
        base_dir = os.path.dirname(__file__)
        if version == "optimized":
            truss_path = os.path.join(base_dir, "truss_optimized.txt")
            result_path = os.path.join(base_dir, "result_optimized.txt")
            html_filename = "_truss_3d_view_optimized.html"
            title = "3D Truss Viewer (Optimized)"
        else:
            truss_path = os.path.join(base_dir, "truss.txt")
            result_path = os.path.join(base_dir, "result.txt")
            html_filename = "_truss_3d_view.html"
            title = "3D Truss Viewer"

        self._append_message("System", f"Building 3D view ({version})...", "system")

        def build():
            try:
                import truss_viewer_3d as tv
                html, elements_data = tv.build_viewer_html(truss_path, result_path)
                out_path = os.path.join(base_dir, html_filename)
                with open(out_path, 'w', encoding='utf-8') as f:
                    f.write(html)

                n_elems = len(elements_data)
                max_u = max((e['utilization'] for e in elements_data), default=0)
                self.after(0, lambda p=out_path, t=title: self._open_viewer_window(p, t))
                info_msg = f"3D viewer opened ({version}): {n_elems} elements, max U={max_u:.2f}"
                self.after(0, lambda m=info_msg: self._append_message("System", m, "system"))
            except Exception as exc:
                msg = f"3D viewer error: {exc}"
                self.after(0, lambda m=msg: self._append_message("System", m, "system"))

        threading.Thread(target=build, daemon=True).start()

    def _open_viewer_window(self, html_path, title="3D Truss Viewer"):
        """Launch the PyQt5 viewer subprocess."""
        # Kill existing viewer if open
        if self._viewer_process and self._viewer_process.poll() is None:
            self._viewer_process.terminate()

        viewer_script = os.path.join(os.path.dirname(__file__), "truss_viewer_window.py")
        python_exe = sys.executable
        try:
            self._viewer_process = subprocess.Popen(
                [python_exe, viewer_script, html_path, title],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._append_message("System", f"Failed to open 3D viewer: {e}", "system")
            webbrowser.open(f'file:///{html_path}')

    # ════════════════════════════════════════════════════════
    # Plot display
    # ════════════════════════════════════════════════════════

    def display_plot(self, image_or_path, title="Plot"):
        if not PIL_AVAILABLE:
            self._append_message("System", "Pillow not installed. Cannot display plots.", "system")
            return
        try:
            if isinstance(image_or_path, str):
                image = Image.open(image_or_path)
            elif hasattr(image_or_path, 'savefig'):
                buf = BytesIO()
                image_or_path.savefig(buf, format='png', dpi=100, bbox_inches='tight',
                                       facecolor=COLOR_BG_DARK, edgecolor='none')
                buf.seek(0)
                image = Image.open(buf)
            else:
                image = image_or_path

            self.plot_image = image
            self.plot_canvas.update_idletasks()
            cw = max(self.plot_canvas.winfo_width(), 50)
            ch = max(self.plot_canvas.winfo_height(), 50)
            scale = min(cw / image.width, ch / image.height, 1.0)
            nw, nh = int(image.width * scale), int(image.height * scale)
            resized = image.resize((nw, nh), Image.Resampling.LANCZOS)
            self.plot_photo = ImageTk.PhotoImage(resized)
            self.plot_canvas.delete("all")
            self.plot_canvas.create_image(cw // 2, ch // 2, image=self.plot_photo, anchor=tk.CENTER)
            self.plot_info_var.set(title)
        except Exception as e:
            self._append_message("System", f"Failed to display plot: {str(e)}", "system")

    def clear_plot(self):
        self.plot_image = None
        self.plot_photo = None
        self.plot_canvas.delete("all")
        self.plot_placeholder_id = self.plot_canvas.create_text(
            150, 80, text="Plots appear here", fill="#b0b0b0",
            font=("Segoe UI", 10), justify=tk.CENTER,
        )
        self.plot_info_var.set("No plot")
        self._append_message("System", "Plot cleared.", "system")

    def save_plot(self):
        if self.plot_image is None:
            self._append_message("System", "No plot to save.", "system")
            return
        try:
            filepath = ctk.filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG files", "*.png"), ("All files", "*.*")],
                title="Save Plot",
            )
            if filepath:
                self.plot_image.save(filepath, "PNG")
                self._append_message("System", f"Plot saved to: {filepath}", "system")
        except Exception as e:
            self._append_message("System", f"Failed to save plot: {str(e)}", "system")

    # ════════════════════════════════════════════════════════
    # Agent init
    # ════════════════════════════════════════════════════════

    def init_agent_async(self):
        def init():
            global agent
            try:
                from truss_agent import create_agent
                agent = create_agent()
                self.agent = agent
                self.after(0, self.on_agent_ready)
            except Exception as e:
                self.after(0, lambda: self.on_agent_error(str(e)))
        threading.Thread(target=init, daemon=True).start()

    def on_agent_ready(self):
        self.status_var.set("Ready")
        self._append_message("System", "AI Agent ready for natural language queries.", "system")

    def on_agent_error(self, error):
        self.status_var.set("Agent error (tools still work)")
        self._append_message("System", f"AI Agent failed to load: {error}", "system")
        self._append_message("System", "Quick action buttons will still work!", "system")

    # ════════════════════════════════════════════════════════
    # Direct tool calls
    # ════════════════════════════════════════════════════════

    def action_check_autocad(self):
        if self.is_processing:
            return
        self.set_processing(True, "Checking AutoCAD...")
        self._append_message("Tool", "Checking AutoCAD connection...", "tool")

        def run():
            try:
                result = truss_tools.check_autocad_connection.invoke({})
                self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_get_truss(self):
        if self.is_processing:
            return
        self.set_processing(True, "Extracting truss...")
        self._append_message("Tool", "Extracting truss from AutoCAD...", "tool")
        self._append_message("System", "Please switch to AutoCAD, select truss elements, then press Enter.", "system")

        def run():
            try:
                result = truss_tools.get_truss_from_autocad.invoke({})
                self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_run_analysis(self):
        if self.is_processing:
            return

        # Ask user for analysis type
        dialog = ctk.CTkToplevel(self)
        dialog.title("Run Analysis")
        dialog.geometry("320x160")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog, text="Select analysis type:",
            font=ctk.CTkFont(family="Segoe UI", size=14),
        ).pack(pady=(20, 14))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack()

        def _start(is_nonlinear):
            dialog.destroy()
            mode = "Non-linear" if is_nonlinear else "Linear"
            self.set_processing(True, f"Running {mode} analysis...")
            self._append_message("Tool", f"Running {mode} 3D truss analysis with OpenSees...", "tool")

            def run():
                try:
                    result = truss_tools.run_truss_analysis.invoke({
                        "geometric_nonlinearity": is_nonlinear,
                        "material_nonlinearity": is_nonlinear,
                    })
                    self.after(0, lambda: self.on_tool_result(result))
                except Exception as e:
                    self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
            threading.Thread(target=run, daemon=True).start()

        ctk.CTkButton(btn_frame, text="Linear", width=120,
                      command=lambda: _start(False)).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="Non-linear", width=120,
                      command=lambda: _start(True)).pack(side="left", padx=8)

    def action_draw_truss(self):
        if self.is_processing:
            return

        # Check if optimized file exists to offer choice
        optimized_exists = os.path.exists(truss_tools.OPTIMIZED_TRUSS_FILE)

        if optimized_exists:
            # Show choice dialog
            choice_win = ctk.CTkToplevel(self)
            choice_win.title("Draw Truss")
            choice_win.geometry("320x160")
            choice_win.resizable(False, False)
            choice_win.transient(self)
            choice_win.grab_set()

            label = ctk.CTkLabel(choice_win, text="Which truss do you want to draw?",
                                 font=ctk.CTkFont(size=14))
            label.pack(pady=(20, 15))

            btn_frame = ctk.CTkFrame(choice_win, fg_color="transparent")
            btn_frame.pack(pady=5)

            def draw_version(version):
                choice_win.destroy()
                self._start_draw(version)

            ctk.CTkButton(btn_frame, text="Original", width=120,
                          command=lambda: draw_version("original")).pack(side="left", padx=8)
            ctk.CTkButton(btn_frame, text="Optimized", width=120,
                          command=lambda: draw_version("optimized")).pack(side="left", padx=8)
        else:
            self._start_draw("original")

    def _start_draw(self, version):
        label = "optimized" if version == "optimized" else "original"
        self.set_processing(True, f"Drawing {label} truss...")
        self._append_message("Tool", f"Drawing {label} truss in AutoCAD...", "tool")
        self._append_message("System", "Please switch to AutoCAD and click to select the insertion point.", "system")

        def run():
            try:
                result = truss_tools.draw_truss_to_autocad.invoke({"version": version})
                self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_3d_solids(self):
        if self.is_processing:
            return
        self.set_processing(True, "Converting to 3D solids...")
        self._append_message("Tool", "Converting truss bars to 3D solids in AutoCAD...", "tool")
        self._append_message(
            "System",
            "Please switch to AutoCAD, select the truss elements, then press Enter.",
            "system",
        )

        def run():
            try:
                result = truss_tools.convert_bars_to_3d_solids.invoke({})
                self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))

        threading.Thread(target=run, daemon=True).start()

    def action_query_data(self):
        if self.is_processing:
            return
        self.set_processing(True, "Reading data...")
        self._append_message("Tool", "Reading truss.txt and result.txt...", "tool")

        def run():
            try:
                truss_exists = os.path.exists(truss_tools.TRUSS_FILE)
                result_exists = os.path.exists(truss_tools.RESULT_FILE)
                info = "Data files status:\n"
                info += f"  truss.txt:  {'Found' if truss_exists else 'Not found'}\n"
                info += f"  result.txt: {'Found' if result_exists else 'Not found'}\n"
                if truss_exists:
                    info += "\nUse the chat to ask specific questions about the data."
                self.after(0, lambda: self.on_tool_result(info))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_assess_ec3(self):
        if self.is_processing:
            return
        self.set_processing(True, "Assessing EC3...")
        self._append_message("Tool", "Assessing analysis results against Eurocode 3 (RAG)...", "tool")

        def run():
            try:
                result = truss_tools.assess_eurocode_compliance.invoke({"check_type": "all"})
                if self.agent:
                    self.after(0, lambda: self._append_message("Result", "RAG context retrieved. Sending to AI...", "system"))
                    prompt = (
                        "Based on the following Eurocode 3 compliance assessment data, "
                        "provide a clear summary of the design checks. Show unity checks "
                        "(N_Ed / N_Rd) for each member and state PASS or FAIL.\n\n"
                        + result
                    )
                    response = self.agent.chat(prompt)
                    self.after(0, lambda: self.on_tool_result(response))
                else:
                    self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_optimize(self):
        if self.is_processing:
            return

        # Open file dialog to select section catalog CSV
        csv_path = ctk.filedialog.askopenfilename(
            title="Select Section Catalog CSV",
            initialdir=os.path.dirname(__file__),
            initialfile="CHS_section.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return  # User cancelled

        # Show algorithm + analysis type selection dialog
        algo_win = ctk.CTkToplevel(self)
        algo_win.title("Optimization Settings")
        algo_win.geometry("360x400")
        algo_win.resizable(False, False)
        algo_win.transient(self)
        algo_win.grab_set()

        ctk.CTkLabel(algo_win, text="Analysis type for optimization:",
                     font=ctk.CTkFont(size=14)).pack(pady=(18, 6))

        analysis_type_var = tk.StringVar(value="nonlinear")
        type_frame = ctk.CTkFrame(algo_win, fg_color="transparent")
        type_frame.pack(pady=(0, 10))
        for val, label in [("linear", "Linear"), ("nonlinear", "Non-linear")]:
            ctk.CTkRadioButton(type_frame, text=label, variable=analysis_type_var, value=val,
                               font=ctk.CTkFont(size=13)).pack(side="left", padx=12)

        ctk.CTkFrame(algo_win, height=1, fg_color=COLOR_BORDER).pack(fill="x", padx=20, pady=(4, 10))

        ctk.CTkLabel(algo_win, text="Select optimization algorithm:",
                     font=ctk.CTkFont(size=14)).pack(pady=(0, 8))

        btn_frame = ctk.CTkFrame(algo_win, fg_color="transparent")
        btn_frame.pack(pady=5)

        def start_opt(algo_name):
            is_nonlinear = analysis_type_var.get() == "nonlinear"
            algo_win.destroy()
            self._run_optimize(csv_path, algo_name, is_nonlinear)

        for name, desc in [("PSO", "Particle Swarm"), ("GA", "Genetic Algorithm"),
                           ("DE", "Differential Evol."), ("ALL", "Run all 3, pick best")]:
            ctk.CTkButton(btn_frame, text=f"{name}\n{desc}", width=150, height=36,
                          font=ctk.CTkFont(size=12),
                          command=lambda n=name: start_opt(n)).pack(pady=3)

    def _run_optimize(self, csv_path, algorithm, is_nonlinear=True):
        self.set_processing(True, "Optimizing sections...")
        csv_name = os.path.basename(csv_path)
        mode_label = "Non-linear" if is_nonlinear else "Linear"
        self._append_message("Tool", f"Running structural optimization (pymoo {algorithm}, {mode_label}, catalog: {csv_name})...", "tool")
        if algorithm == "ALL":
            self._append_message("System", "Running PSO, GA, and DE — this may take a few minutes.", "system")
        else:
            self._append_message("System", "This may take a minute depending on pop_size and n_gen.", "system")

        def run():
            try:
                result = truss_tools.optimize_truss_sections.invoke({
                    "mode": "discrete",
                    "section_csv": csv_path,
                    "algorithm": algorithm,
                    "pop_size": 50,
                    "n_gen": 100,
                })

                # Run analysis on optimized truss to produce result_optimized.txt
                optimized_truss = os.path.join(os.path.dirname(__file__), "truss_optimized.txt")
                if os.path.exists(optimized_truss):
                    self.after(0, lambda: self._append_message(
                        "Tool", f"Analyzing optimized truss ({mode_label})...", "tool"))
                    import truss3D_analysis_from_file
                    result_opt = os.path.join(os.path.dirname(__file__), "result_optimized.txt")
                    truss3D_analysis_from_file.run_truss_analysis(
                        optimized_truss, result_opt,
                        is_geom_nonlin=is_nonlinear, is_mat_nonlin=is_nonlinear)

                self.after(0, lambda: self.on_tool_result(result))
            except Exception as e:
                self.after(0, lambda: self.on_tool_result(f"Error: {str(e)}"))
        threading.Thread(target=run, daemon=True).start()

    def action_compare_solvers(self):
        if self.is_processing:
            return

        base = os.path.dirname(__file__)
        sap_file = os.path.join(base, "sap2000_results.txt")
        oss_file = os.path.join(base, "result.txt")
        missing = [f for f in (sap_file, oss_file) if not os.path.exists(f)]
        if missing:
            self._append_message("System",
                f"Missing result file(s): {', '.join(os.path.basename(f) for f in missing)}. "
                "Run both analyses first.", "system")
            return

        self.set_processing(True, "Comparing solvers...")
        self._append_message("Tool", "Comparing nodal displacements and axial forces (Live Load): SAP2000 vs OpenSees...", "tool")

        def _parse_sap2000(filepath):
            """Return {joint_id(int): (UX_mm, UY_mm, UZ_mm)} for the LIVE load pattern."""
            joints = {}
            in_live = False   # inside the LIVE load-pattern block
            in_sec  = False   # inside JOINT DISPLACEMENTS table
            with open(filepath) as f:
                for line in f:
                    # detect start of a load-pattern block
                    if "Load pattern" in line or "Load case" in line:
                        in_live = "LIVE" in line.upper()
                        in_sec  = False
                        continue
                    if not in_live:
                        continue
                    if "JOINT DISPLACEMENTS" in line:
                        in_sec = True
                        continue
                    if not in_sec:
                        continue
                    s = line.strip()
                    if not s or s.startswith("Joint") or s.startswith("---") or s.startswith("("):
                        continue
                    if s.startswith("FRAME") or s.startswith("SUPPORT") or s.startswith("="):
                        break
                    parts = s.split()
                    if len(parts) >= 4:
                        try:
                            jid = int(parts[0])
                            joints[jid] = (float(parts[1]),
                                           float(parts[2]),
                                           float(parts[3]))
                        except ValueError:
                            pass
            return joints

        def _parse_opensees(filepath):
            """Return {joint_id(int): (dx_mm, dy_mm, dz_mm)}"""
            joints = {}
            in_sec = False
            with open(filepath) as f:
                for line in f:
                    if "JOINT DISPLACEMENTS" in line:
                        in_sec = True
                        continue
                    if not in_sec:
                        continue
                    s = line.strip()
                    if not s or s.startswith("Joint") or s.startswith("---") or s.startswith("="):
                        continue
                    parts = s.split()
                    if len(parts) >= 4:
                        try:
                            jid = int(parts[0])
                            joints[jid] = (float(parts[1]),
                                           float(parts[2]),
                                           float(parts[3]))
                        except ValueError:
                            pass
                    elif parts and parts[0].startswith("="):
                        break
            return joints

        def _parse_sap2000_axial(filepath):
            """Return {elem_id(int): axial_kN(float)} from FRAME MEMBER AXIAL FORCES section."""
            elems = {}
            in_sec = False
            with open(filepath) as f:
                for line in f:
                    if "FRAME MEMBER AXIAL FORCES" in line:
                        in_sec = True
                        continue
                    if not in_sec:
                        continue
                    s = line.strip()
                    if not s or s.startswith("(") or s.startswith("Elem") or s.startswith("---"):
                        continue
                    if s.startswith("SUPPORT") or s.startswith("="):
                        break
                    parts = s.split()
                    # format: elem  section  ji  jj  L  axial_kN  type
                    if len(parts) >= 6:
                        try:
                            eid = int(parts[0])
                            axial = float(parts[-2])   # second-to-last is axial (kN)
                            elems[eid] = axial
                        except ValueError:
                            pass
            return elems

        def _parse_opensees_axial(filepath):
            """Return {elem_id(int): axial_kN(float)} from MEMBER AXIAL FORCES section (N→kN)."""
            elems = {}
            in_sec = False
            with open(filepath) as f:
                for line in f:
                    if "MEMBER AXIAL FORCES" in line:
                        in_sec = True
                        continue
                    if not in_sec:
                        continue
                    s = line.strip()
                    if not s or s.startswith("Elem") or s.startswith("---") or s.startswith("="):
                        continue
                    parts = s.split()
                    # format: elem  section  area_mm2  force_N  stress_MPa
                    if len(parts) >= 4:
                        try:
                            eid = int(parts[0])
                            force_kN = float(parts[3]) / 1000.0   # N → kN
                            elems[eid] = force_kN
                        except ValueError:
                            pass
            return elems

        def run():
            try:
                import math

                sap = _parse_sap2000(sap_file)
                oss = _parse_opensees(oss_file)

                common = sorted(set(sap) & set(oss))
                if not common:
                    self.after(0, lambda: self.on_tool_result(
                        "No common joints found between the two result files."))
                    return

                sap_mag = [math.sqrt(sum(v**2 for v in sap[j])) for j in common]
                oss_mag = [math.sqrt(sum(v**2 for v in oss[j])) for j in common]

                FREE_TOL = 1e-6
                free_idx   = [i for i, s in enumerate(sap_mag) if s > FREE_TOL]
                common_f   = [common[i]  for i in free_idx]
                sap_mag_f  = [sap_mag[i] for i in free_idx]
                oss_mag_f  = [oss_mag[i] for i in free_idx]

                sap_ax = _parse_sap2000_axial(sap_file)
                oss_ax = _parse_opensees_axial(oss_file)
                common_e = sorted(set(sap_ax) & set(oss_ax))
                sap_axf  = [sap_ax[e] for e in common_e]
                oss_axf  = [oss_ax[e] for e in common_e]

                # ── Store data for direction-switch redraws ───────────────────
                self._cmp_sap    = sap
                self._cmp_oss    = oss
                self._cmp_sap_ax = sap_ax
                self._cmp_oss_ax = oss_ax

                # ── Generate plot (uses current direction selector) ───────────
                self._draw_compare_plot(sap, oss, sap_ax, oss_ax,
                                        self._disp_dir_var.get())

                # ── Text summary ──────────────────────────────────────────────
                ax_diff  = [o - s for s, o in zip(sap_axf, oss_axf)]
                ax_pct   = [(o - s) / abs(s) * 100.0 if abs(s) > 1e-6 else 0.0
                            for s, o in zip(sap_axf, oss_axf)]
                ranked_e = sorted(zip(ax_pct, common_e), key=lambda t: abs(t[0]), reverse=True)[:5]

                disp_abs_mm  = [abs(o - s) for s, o in zip(sap_mag_f, oss_mag_f)]
                disp_pct     = [abs((o - s) / s * 100.0) for s, o in zip(sap_mag_f, oss_mag_f)]
                max_disp_mm  = max(disp_abs_mm)
                avg_disp_mm  = sum(disp_abs_mm) / len(disp_abs_mm)
                max_disp_pct = max(disp_pct)

                sample_j = common_f[:5]

                lines = [
                    "=== SAP2000 vs OpenSees COMPARISON [Live Load] ===",
                    f"  SAP2000  ← {os.path.basename(sap_file)}",
                    f"  OpenSees ← {os.path.basename(oss_file)}",
                    f"  Common joints parsed: {len(common)}  |  free (|U|>0): {len(common_f)}",
                    "",
                    "── Nodal Displacements (independent parse from each file) ──",
                    f"  Max |ΔU| : {max_disp_mm:.4f} mm  ({max_disp_pct:.4f} %)",
                    f"  Avg |ΔU| : {avg_disp_mm:.4f} mm",
                    "",
                    f"  Sample — first {len(sample_j)} free joints:",
                    f"  {'Jt':>4}  {'SAP2000|U|(mm)':>16}  {'OpenSees|U|(mm)':>16}  {'Δ(mm)':>10}",
                    "  " + "-" * 52,
                ]
                for j in sample_j:
                    sm = math.sqrt(sum(v**2 for v in sap[j]))
                    om = math.sqrt(sum(v**2 for v in oss[j]))
                    lines.append(f"  {j:>4}  {sm:>16.4f}  {om:>16.4f}  {(om - sm):>+10.4f}")
                lines += [
                    "",
                    "── Member Axial Forces ──",
                    f"  Common elements : {len(common_e)}",
                    f"  Top-5 differences (SAP2000 = reference):",
                    f"  {'Elem':>6}  {'SAP2000 (kN)':>14}  {'OpenSees (kN)':>14}  {'Δ (kN)':>10}  {'Δ (%)':>8}",
                    "  " + "-" * 62,
                ]
                for pct, e in ranked_e:
                    idx = common_e.index(e)
                    diff = ax_diff[idx]
                    lines.append(
                        f"  {e:>6}  {sap_axf[idx]:>14.3f}  "
                        f"{oss_axf[idx]:>14.3f}  {diff:>+10.3f}  {pct:>+8.2f}%")
                lines += ["", "Plot displayed in the plot panel.  Use the Disp selector to switch directions."]
                result_text = "\n".join(lines)

                self.after(0, lambda: self.on_tool_result(result_text))

            except Exception as e:
                import traceback
                self.after(0, lambda: self.on_tool_result(
                    f"Compare error: {e}\n{traceback.format_exc()}"))

        threading.Thread(target=run, daemon=True).start()

    def action_sap2000(self):
        if self.is_processing:
            return
        self.set_processing(True, "Building SAP2000 model...")
        self._append_message("Tool", "Building and analysing truss in SAP2000 via OAPI...", "tool")
        self._append_message("System", "SAP2000 must be running (or installed). Auto-timeout in 3 min — or use 'Force Unlock' if stuck.", "system")

        # Cancel any previous timeout that may still be pending
        if self._sap_timeout_id:
            self.after_cancel(self._sap_timeout_id)
        self._sap_timeout_id = self.after(180_000, self._sap2000_timeout)

        def run():
            try:
                import io, contextlib
                import sap2000_truss
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    sap2000_truss.build_model()
                result = buf.getvalue() or "SAP2000 model built successfully."
                self.after(0, lambda: self._sap2000_finish(result))
            except Exception as e:
                self.after(0, lambda: self._sap2000_finish(f"SAP2000 error: {str(e)}"))

        threading.Thread(target=run, daemon=True).start()

    def _sap2000_finish(self, result):
        """Called when the SAP2000 thread completes (success or error)."""
        if self._sap_timeout_id:
            self.after_cancel(self._sap_timeout_id)
            self._sap_timeout_id = None
        self.on_tool_result(result)

    def _sap2000_timeout(self):
        """Called automatically after 3 min if SAP2000 has not responded."""
        self._sap_timeout_id = None
        self._append_message("System",
            "SAP2000 timed out (3 min) — processing lock released. "
            "SAP2000 may still be running in the background. "
            "You can now use other tools.", "system")
        self.set_processing(False)

    def _force_unlock(self):
        """Manually release the processing lock without restarting the chatbot."""
        if self._sap_timeout_id:
            self.after_cancel(self._sap_timeout_id)
            self._sap_timeout_id = None
        if self.is_processing:
            self._append_message("System",
                "Processing lock force-released. "
                "Any background task (SAP2000, etc.) may still be running.", "system")
            self.set_processing(False)
        else:
            self._append_message("System", "No active lock to release.", "system")

    def on_tool_result(self, result):
        self._append_message("Result", result, "bot")
        self.set_processing(False)
        # Auto-refresh 3D viewer if truss data may have changed
        truss_file = os.path.join(os.path.dirname(__file__), "truss.txt")
        if os.path.exists(truss_file):
            self.refresh_3d_viewer()

    # ════════════════════════════════════════════════════════
    # Chat with AI
    # ════════════════════════════════════════════════════════

    def on_send(self, event=None):
        message = self.input_entry.get().strip()
        if message:
            self.input_entry.delete(0, "end")
            self.send_message(message)

    def send_message(self, message):
        if self.is_processing:
            return
        self._append_message("You", message, "user")
        if not self.agent:
            self._append_message("System", "AI Agent not available. Use Quick Actions instead.", "system")
            return
        self.set_processing(True, "AI is thinking...")

        def process():
            try:
                response = self.agent.chat(message)
                self.after(0, lambda: self.on_response(response))
            except Exception as e:
                self.after(0, lambda: self.on_response(f"Error: {str(e)}"))
        threading.Thread(target=process, daemon=True).start()

    def on_response(self, response):
        self._append_message("Assistant", response, "bot")
        self.set_processing(False)

    def clear_chat(self):
        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")
        self.chat_display.configure(state="disabled")
        if self.agent:
            self.agent.clear_history()
        self._append_message("System", "Chat cleared.", "system")


def main():
    """Main entry point."""
    print("=" * 60)
    print("TRUSS ANALYSIS CHATBOT")
    print("=" * 60)

    app = TrussChatbot()
    app.mainloop()


if __name__ == "__main__":
    main()
