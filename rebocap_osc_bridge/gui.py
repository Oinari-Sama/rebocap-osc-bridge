"""
gui.py — rebocap-osc-bridge GUI

tkinter を使ったシンプルな GUI。
非エンジニアのユーザーが CLI なしで操作できる。

使い方:
    rebocap-osc-bridge --gui
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

from .config import BridgeConfig, save_config, validate_config


def launch_gui(cfg: BridgeConfig) -> None:
    """GUI アプリを起動する。cfg はデフォルト値として利用される。"""
    app = BridgeApp(cfg)
    app.mainloop()


class _LogHandler(logging.Handler):
    """logging のレコードを tkinter の ScrolledText に転送するハンドラ。"""

    def __init__(self, widget: scrolledtext.ScrolledText):
        super().__init__()
        self._widget = widget

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        # tkinter の操作は必ずメインスレッドから
        try:
            self._widget.after(0, self._append, msg, record.levelno)
        except RuntimeError:
            pass  # ウィジェットが破棄された後の呼び出しを無視

    def _append(self, msg: str, levelno: int) -> None:
        self._widget.configure(state="normal")
        tag = {
            logging.DEBUG:   "debug",
            logging.INFO:    "info",
            logging.WARNING: "warning",
            logging.ERROR:   "error",
        }.get(levelno, "info")
        self._widget.insert(tk.END, msg, tag)
        self._widget.see(tk.END)
        self._widget.configure(state="disabled")


class BridgeApp(tk.Tk):
    """メインウィンドウ。"""

    ALL_SLOTS = [
        "hip", "chest",
        "left_foot", "right_foot",
        "left_knee", "right_knee",
        "left_elbow", "right_elbow",
    ]

    def __init__(self, cfg: BridgeConfig):
        super().__init__()
        self.title("rebocap-osc-bridge")
        self.resizable(True, True)
        self.minsize(600, 560)
        self._cfg = cfg
        self._runner: Optional[object] = None  # BridgeRunner (遅延インポート)
        self._fps_after_id: Optional[str] = None

        self._build_ui()
        self._apply_cfg_to_ui(cfg)
        self._setup_log_handler()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ── 接続設定 ──────────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self, text="接続設定")
        conn_frame.pack(fill="x", **pad)

        # Rebocap
        ttk.Label(conn_frame, text="Rebocap ホスト:").grid(row=0, column=0, sticky="e", **pad)
        self._rebocap_host = ttk.Entry(conn_frame, width=20)
        self._rebocap_host.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(conn_frame, text="ポート:").grid(row=0, column=2, sticky="e", **pad)
        self._rebocap_port = ttk.Entry(conn_frame, width=8)
        self._rebocap_port.grid(row=0, column=3, sticky="w", **pad)

        # VRChat OSC
        ttk.Label(conn_frame, text="VRChat OSC IP:").grid(row=1, column=0, sticky="e", **pad)
        self._osc_ip = ttk.Entry(conn_frame, width=20)
        self._osc_ip.grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(conn_frame, text="ポート:").grid(row=1, column=2, sticky="e", **pad)
        self._osc_port = ttk.Entry(conn_frame, width=8)
        self._osc_port.grid(row=1, column=3, sticky="w", **pad)

        # ── トラッカー選択 ─────────────────────────────────────────
        tracker_frame = ttk.LabelFrame(self, text="トラッカースロット")
        tracker_frame.pack(fill="x", **pad)

        self._tracker_vars: dict[str, tk.BooleanVar] = {}
        labels = {
            "hip":         "Hip (腰)",
            "chest":       "Chest (胸)",
            "left_foot":   "Left Foot (左足首)",
            "right_foot":  "Right Foot (右足首)",
            "left_knee":   "Left Knee (左膝)",
            "right_knee":  "Right Knee (右膝)",
            "left_elbow":  "Left Elbow (左肘)",
            "right_elbow": "Right Elbow (右肘)",
        }
        for i, slot in enumerate(self.ALL_SLOTS):
            var = tk.BooleanVar(value=slot in ("hip", "left_foot", "right_foot"))
            self._tracker_vars[slot] = var
            cb = ttk.Checkbutton(tracker_frame, text=labels[slot], variable=var)
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=10, pady=2)

        # ── 詳細設定 ──────────────────────────────────────────────
        adv_frame = ttk.LabelFrame(self, text="詳細設定")
        adv_frame.pack(fill="x", **pad)

        # Feature #3: 身長
        ttk.Label(adv_frame, text="身長 (m):").grid(row=0, column=0, sticky="e", **pad)
        self._height = ttk.Entry(adv_frame, width=8)
        self._height.grid(row=0, column=1, sticky="w", **pad)

        # Feature #8: --send-head
        self._send_head_var = tk.BooleanVar()
        ttk.Checkbutton(adv_frame, text="Head 送信 (HMD/VR モード専用)",
                        variable=self._send_head_var).grid(
            row=0, column=2, columnspan=2, sticky="w", **pad)

        # Feature #6: OSC Debug
        self._osc_debug_var = tk.BooleanVar()
        ttk.Checkbutton(adv_frame, text="OSC デバッグ表示",
                        variable=self._osc_debug_var).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)

        # Feature #7: VMC
        self._vmc_var = tk.BooleanVar()
        self._vmc_cb = ttk.Checkbutton(adv_frame, text="VMC 出力 (一時停止中)",
                                       variable=self._vmc_var,
                                       command=self._on_vmc_toggle,
                                       state="disabled")
        self._vmc_cb.grid(row=1, column=2, sticky="w", **pad)

        ttk.Label(adv_frame, text="VMC IP:").grid(row=2, column=0, sticky="e", **pad)
        self._vmc_ip = ttk.Entry(adv_frame, width=16, state="disabled")
        self._vmc_ip.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(adv_frame, text="VMC Port:").grid(row=2, column=2, sticky="e", **pad)
        self._vmc_port = ttk.Entry(adv_frame, width=8, state="disabled")
        self._vmc_port.grid(row=2, column=3, sticky="w", **pad)

        # Feature #8: OSCQuery
        self._oscquery_var = tk.BooleanVar()
        self._oscquery_cb = ttk.Checkbutton(adv_frame, text="OSCQuery (一時停止中)",
                                            variable=self._oscquery_var,
                                            state="disabled")
        self._oscquery_cb.grid(
            row=3, column=0, columnspan=2, sticky="w", **pad)

        # ── ステータスバー ─────────────────────────────────────────
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", **pad)

        self._status_label = ttk.Label(status_frame, text="停止中",
                                       foreground="gray", width=30)
        self._status_label.pack(side="left")

        self._fps_label = ttk.Label(status_frame, text="--.- fps",
                                    foreground="gray")
        self._fps_label.pack(side="left", padx=16)

        # ── 操作ボタン ─────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", **pad)

        self._start_btn = ttk.Button(btn_frame, text="▶ 開始",
                                     command=self._on_start)
        self._start_btn.pack(side="left", padx=4)

        self._stop_btn = ttk.Button(btn_frame, text="■ 停止",
                                    command=self._on_stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)

        self._save_btn = ttk.Button(btn_frame, text="⚙ config.toml を保存",
                                    command=self._on_save_config)
        self._save_btn.pack(side="left", padx=4)

        # ── ログビュー ─────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="ログ")
        log_frame.pack(fill="both", expand=True, **pad)

        self._log_widget = scrolledtext.ScrolledText(
            log_frame, height=10, state="disabled",
            font=("Courier", 9), wrap="word",
        )
        self._log_widget.pack(fill="both", expand=True)

        # ログの色分け
        self._log_widget.tag_config("debug",   foreground="#888888")
        self._log_widget.tag_config("info",    foreground="#000000")
        self._log_widget.tag_config("warning", foreground="#b07000")
        self._log_widget.tag_config("error",   foreground="#cc0000")

    def _setup_log_handler(self) -> None:
        handler = _LogHandler(self._log_widget)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        logging.getLogger().addHandler(handler)

    # ------------------------------------------------------------------
    # UI ↔ Config
    # ------------------------------------------------------------------

    def _apply_cfg_to_ui(self, cfg: BridgeConfig) -> None:
        self._rebocap_host.delete(0, tk.END)
        self._rebocap_host.insert(0, cfg.rebocap_host)

        self._rebocap_port.delete(0, tk.END)
        self._rebocap_port.insert(0, str(cfg.rebocap_port))

        self._osc_ip.delete(0, tk.END)
        self._osc_ip.insert(0, cfg.osc_ip)

        self._osc_port.delete(0, tk.END)
        self._osc_port.insert(0, str(cfg.osc_port))

        self._height.delete(0, tk.END)
        self._height.insert(0, str(cfg.height_m))

        for slot, var in self._tracker_vars.items():
            var.set(slot in cfg.trackers)

        self._send_head_var.set(cfg.send_head)
        self._osc_debug_var.set(cfg.osc_debug)
        self._vmc_var.set(False)

        self._vmc_ip.configure(state="normal")
        self._vmc_ip.delete(0, tk.END)
        self._vmc_ip.insert(0, cfg.vmc_ip)
        self._vmc_port.configure(state="normal")
        self._vmc_port.delete(0, tk.END)
        self._vmc_port.insert(0, str(cfg.vmc_port))
        if not cfg.vmc_enabled:
            self._vmc_ip.configure(state="disabled")
            self._vmc_port.configure(state="disabled")

        self._oscquery_var.set(False)

    def _read_cfg_from_ui(self) -> Optional[BridgeConfig]:
        """UI の現在値から BridgeConfig を生成する。バリデーションエラー時は None を返す。"""
        cfg = BridgeConfig()
        try:
            cfg.rebocap_host = self._rebocap_host.get().strip()
            cfg.rebocap_port = int(self._rebocap_port.get())
            cfg.osc_ip       = self._osc_ip.get().strip()
            cfg.osc_port     = int(self._osc_port.get())
            cfg.height_m     = float(self._height.get())
        except ValueError as e:
            messagebox.showerror("入力エラー", f"数値の形式が正しくありません:\n{e}")
            return None

        cfg.trackers = [s for s, v in self._tracker_vars.items() if v.get()]
        if not cfg.trackers:
            messagebox.showerror("入力エラー", "トラッカースロットを1つ以上選択してください。")
            return None

        cfg.send_head        = self._send_head_var.get()
        cfg.osc_debug        = self._osc_debug_var.get()
        cfg.vmc_enabled      = False
        cfg.oscquery_enabled = False

        if cfg.vmc_enabled:
            try:
                cfg.vmc_ip   = self._vmc_ip.get().strip()
                cfg.vmc_port = int(self._vmc_port.get())
            except ValueError as e:
                messagebox.showerror("入力エラー", f"VMC 設定の数値が正しくありません:\n{e}")
                return None

        try:
            return validate_config(cfg)
        except ValueError as e:
            messagebox.showerror("入力エラー", str(e))
            return None

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------

    def _on_vmc_toggle(self) -> None:
        state = "normal" if self._vmc_var.get() else "disabled"
        self._vmc_ip.configure(state=state)
        self._vmc_port.configure(state=state)

    def _on_start(self) -> None:
        cfg = self._read_cfg_from_ui()
        if cfg is None:
            return

        from .bridge import BridgeRunner
        self._runner = BridgeRunner(cfg)
        self._runner.start()

        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._set_inputs_state("disabled")
        self._stop_btn.configure(state="normal")

        self._poll_status()

    def _on_stop(self) -> None:
        if self._runner:
            self._runner.stop()
        if self._fps_after_id:
            self.after_cancel(self._fps_after_id)
            self._fps_after_id = None

        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._set_inputs_state("normal")
        self._on_vmc_toggle()  # VMC フィールドの状態を再設定
        self._status_label.configure(text="停止中", foreground="gray")
        self._fps_label.configure(text="--.- fps", foreground="gray")

    def _on_save_config(self) -> None:
        cfg = self._read_cfg_from_ui()
        if cfg is None:
            return
        p = save_config(cfg)
        messagebox.showinfo("設定ファイル", f"config.toml を保存しました:\n{p}")

    def _on_close(self) -> None:
        self._on_stop()
        self.destroy()

    # ------------------------------------------------------------------
    # ステータスポーリング
    # ------------------------------------------------------------------

    def _poll_status(self) -> None:
        if self._runner is None:
            return
        status = self._runner.status
        fps    = self._runner.fps

        # ステータスに応じて色を変える
        if "ストリーミング" in status:
            color = "#007700"
        elif "エラー" in status:
            color = "#cc0000"
        elif "再接続" in status:
            color = "#b07000"
        else:
            color = "gray"

        self._status_label.configure(text=status, foreground=color)
        if "ストリーミング" in status and fps > 0:
            self._fps_label.configure(text=f"{fps:.1f} fps", foreground="#007700")

        if self._runner.is_running:
            self._fps_after_id = self.after(500, self._poll_status)
        else:
            self._on_stop()

    # ------------------------------------------------------------------
    # ヘルパー
    # ------------------------------------------------------------------

    def _set_inputs_state(self, state: str) -> None:
        """全入力ウィジェットを一括で有効/無効にする。"""
        widgets = [
            self._rebocap_host, self._rebocap_port,
            self._osc_ip, self._osc_port,
            self._height,
        ]
        for w in widgets:
            w.configure(state=state)
        for var_cb in self._tracker_vars.values():
            pass  # Checkbutton への state 設定は別途
        # Checkbutton は children から探す
        for child in self.winfo_children():
            self._set_widget_state(child, state)
        self._log_widget.configure(state="disabled")
        self._vmc_cb.configure(state="disabled")
        self._oscquery_cb.configure(state="disabled")

    def _set_widget_state(self, widget, state: str) -> None:
        """再帰的にウィジェットの state を設定する。"""
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._set_widget_state(child, state)
