"""RELAP5 自动化测试图形界面（tkinter，无额外依赖）。"""

import logging
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from common.util_prisma import ROOT, CONFIG_PATH, init_log, read_config, read_table, resolve_path
from run_prisma.executor import execute_testcase


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__(logging.INFO)
        self.log_queue = log_queue

    def emit(self, record):
        try:
            self.log_queue.put_nowait(("log", self.format(record)))
        except Exception:
            pass


class Relap5App:
    def __init__(self, master):
        self.master = master
        master.title("RELAP5 自动化测试")
        master.geometry("840x620")

        self.config = read_config()
        self.log_queue = queue.Queue()
        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        self._build_ui()
        self._load_from_config()

        logger = init_log()
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)

        master.protocol("WM_DELETE_WINDOW", self._on_close)
        master.after(100, self._poll)

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.LabelFrame(self.master, text="运行配置")
        top.pack(fill="x", padx=10, pady=(10, 5))

        self.var_script = tk.StringVar()
        self.var_check = tk.StringVar()
        self.var_exec = tk.StringVar()
        self.var_mode = tk.StringVar(value="simulate")
        self.var_workers = tk.StringVar(value="0")

        rows = [
            ("测试剧本(xlsx)", self.var_script, "script"),
            ("结果表(xlsx)", self.var_check, "check"),
            ("被测程序(relap5.exe / nusolsyslmr.exe)", self.var_exec, "exec"),
        ]
        for i, (label, var, kind) in enumerate(rows):
            ttk.Label(top, text=label).grid(row=i, column=0, sticky="e", padx=5, pady=3)
            ttk.Entry(top, textvariable=var, width=64).grid(row=i, column=1, sticky="we", padx=5)
            ttk.Button(top, text="浏览...",
                       command=lambda k=kind: self._browse(k)).grid(row=i, column=2, padx=5)
        top.columnconfigure(1, weight=1)

        mode_frame = ttk.Frame(top)
        mode_frame.grid(row=3, column=1, sticky="w", pady=3)
        ttk.Radiobutton(mode_frame, text="模拟运行(无被测程序)",
                        variable=self.var_mode, value="simulate").pack(side="left")
        ttk.Radiobutton(mode_frame, text="真实运行被测程序",
                        variable=self.var_mode, value="real").pack(side="left", padx=10)
        ttk.Label(mode_frame, text="并行线程数(0=自动)").pack(side="left", padx=(20, 5))
        ttk.Spinbox(mode_frame, from_=0, to=64, textvariable=self.var_workers,
                    width=5).pack(side="left")

        btns = ttk.Frame(self.master)
        btns.pack(fill="x", padx=10, pady=5)
        self.btn_start = ttk.Button(btns, text="▶ 开始执行", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        tree_frame = ttk.LabelFrame(self.master, text="测试用例")
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.tree = ttk.Treeview(tree_frame, columns=("case", "progress", "status"),
                                 show="headings", height=8)
        self.tree.heading("case", text="用例编号")
        self.tree.heading("progress", text="进度")
        self.tree.heading("status", text="状态")
        self.tree.column("case", width=160)
        self.tree.column("progress", width=120, anchor="center")
        self.tree.column("status", width=180, anchor="center")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(self.master, text="运行日志")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="none")
        ysb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ysb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

    def _browse(self, kind):
        if kind == "exec":
            path = filedialog.askopenfilename(
                filetypes=[("可执行程序", "*.exe"), ("所有文件", "*.*")])
        else:
            path = filedialog.askopenfilename(
                filetypes=[("Excel", "*.xlsx"), ("所有文件", "*.*")])
        if not path:
            return
        {"script": self.var_script, "check": self.var_check,
         "exec": self.var_exec}[kind].set(path)
        if kind == "script":
            self._load_cases(path)

    def _load_cases(self, path):
        self.tree.delete(*self.tree.get_children())
        try:
            commands = read_table(path)
        except Exception as exc:
            messagebox.showerror("错误", f"读取剧本失败:\n{exc}")
            return
        totals = {}
        order = []
        for cmd in commands:
            case_no = str(cmd[0])
            if case_no not in totals:
                totals[case_no] = 0
                order.append(case_no)
            totals[case_no] += 1
        for case_no in order:
            self.tree.insert("", "end", iid=case_no,
                             values=(case_no, f"0/{totals[case_no]}", "等待中"))

    def _load_from_config(self):
        for key, var in (("dataConfigPath", self.var_script),
                         ("checkConfigPath", self.var_check)):
            v = self.config.get(key)
            if v:
                var.set(str(resolve_path(v)))
        self.var_mode.set(str(self.config.get("runMode", "simulate")))
        self.var_workers.set(str(self.config.get("maxWorkers", 0)))
        exe = ROOT / "inputs" / str(self.config.get("executableName", "relap5.exe"))
        if exe.is_file():
            self.var_exec.set(str(exe))
        script = self.var_script.get()
        if script and Path(script).is_file():
            self._load_cases(script)

    @staticmethod
    def _rel(p):
        p = Path(p).resolve()
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)

    # ---------- actions ----------
    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        script = Path(self.var_script.get())
        if not script.is_file():
            messagebox.showwarning("提示", "请先选择测试剧本文件")
            return
        check = Path(self.var_check.get())
        if not check.is_file():
            messagebox.showwarning("提示", "请先选择结果表文件")
            return
        try:
            workers = int(self.var_workers.get())
        except ValueError:
            workers = 0

        exe = Path(self.var_exec.get())
        if exe.is_file():
            inputs_dir = ROOT / "inputs"
            inputs_dir.mkdir(exist_ok=True)
            target = inputs_dir / exe.name
            if not target.exists():
                shutil.copy2(exe, target)

        cfg = read_config()
        cfg["dataConfigPath"] = self._rel(script)
        cfg["checkConfigPath"] = self._rel(check)
        cfg["runMode"] = self.var_mode.get()
        cfg["maxWorkers"] = workers
        if exe.is_file():
            cfg["executableName"] = exe.name
        CONFIG_PATH.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

        self.stop_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._load_cases(str(script))
        self.worker = threading.Thread(
            target=self._run_worker, args=(script, workers), daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.btn_stop.config(state="disabled")

    def _run_worker(self, script, workers):
        def progress(case_no, index, total, ok):
            self.ui_queue.put(("step", case_no, index, total, ok))

        try:
            results = execute_testcase(
                file_path=str(script), max_workers=workers,
                stop_event=self.stop_event, progress_cb=progress)
            status = "已停止" if self.stop_event.is_set() else "执行完成"
            self.ui_queue.put(("summary", results, status))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    # ---------- queue polling ----------
    def _poll(self):
        try:
            while True:
                _, text = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", text + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                self._handle_ui(self.ui_queue.get_nowait())
        except queue.Empty:
            pass
        self.master.after(100, self._poll)

    def _handle_ui(self, msg):
        kind = msg[0]
        if kind == "step":
            _, case_no, index, total, ok = msg
            if not self.tree.exists(case_no):
                return
            status = "失败，已中断" if not ok else ("执行中" if index < total else "已执行")
            self.tree.set(case_no, "progress", f"{index}/{total}")
            self.tree.set(case_no, "status", status)
        elif kind == "summary":
            _, results, status = msg
            for case_no, ok in results.items():
                if self.tree.exists(case_no):
                    self.tree.set(case_no, "status", "通过" if ok else "不通过/中断")
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            passed = sum(1 for v in results.values() if v)
            messagebox.showinfo(
                "执行结束",
                f"{status}：通过 {passed}/{len(results)}\n结果已回填到结果表。")
        elif kind == "error":
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            messagebox.showerror("执行失败", msg[1])

    def _on_close(self):
        self.stop_event.set()
        self.master.destroy()


def main():
    root = tk.Tk()
    Relap5App(root)
    root.mainloop()


if __name__ == "__main__":
    main()