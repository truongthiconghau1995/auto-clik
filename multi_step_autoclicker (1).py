"""
Multi-Step Auto Clicker — app tự động hóa quy trình lặp lại nhiều bước
(click -> copy -> click -> paste -> click -> nhập mã -> lặp lại)

Bản này KHÔNG dùng thư viện "keyboard" (bỏ phím tắt toàn cục F5/F8) vì thư viện
đó cần quyền Administrator và hay bị phần mềm bảo mật công ty chặn/cảnh báo.
Dùng nút Bắt đầu / Dừng ngay trong cửa sổ app thay thế — vẫn hoạt động bình
thường vì automation chạy ở luồng nền, cửa sổ app vẫn bấm được trong lúc chạy.

Dừng khẩn cấp: rê chuột lên góc TRÊN-TRÁI màn hình (tính năng có sẵn của
pyautogui, không cần quyền admin).

LƯU Ý: build file .exe này cần một máy có Python + pip (ví dụ máy nhà riêng),
vì máy công ty bị khóa hoàn toàn không cài được gì. Xem hướng dẫn build bằng
GitHub Actions (chỉ cần trình duyệt, không cần CMD) ở phần chat.
"""

import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import pyautogui
import pyperclip

pyautogui.FAILSAFE = True  # đưa chuột vào góc trên-trái màn hình để dừng khẩn cấp
pyautogui.PAUSE = 0.05

CONFIG_FILE = "autoclicker_steps.json"

ACTIONS = [
    "Chỉ click",
    "Click + Copy (Ctrl+C)",
    "Click + Paste (Ctrl+V)",
    "Click + Nhập mã",
]


class Step:
    def __init__(self, action=ACTIONS[0], x=None, y=None, text="", press_enter=False):
        self.action = action
        self.x = x
        self.y = y
        self.text = text
        self.press_enter = press_enter

    def to_dict(self):
        return {
            "action": self.action,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "press_enter": self.press_enter,
        }

    @staticmethod
    def from_dict(d):
        return Step(d["action"], d["x"], d["y"], d.get("text", ""), d.get("press_enter", False))

    def label(self):
        pos = f"({self.x},{self.y})" if self.x is not None else "(chưa đặt)"
        extra = f" | text='{self.text}'" if self.action == "Click + Nhập mã" else ""
        return f"{self.action} @ {pos}{extra}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("Multi-Step Auto Clicker")
        root.geometry("560x620")

        self.steps = []
        self.running = False
        self.stop_flag = threading.Event()

        # ---- Danh sách bước ----
        frame_list = ttk.LabelFrame(root, text="Các bước (thực hiện theo thứ tự, rồi lặp lại)")
        frame_list.pack(fill="both", expand=True, padx=10, pady=8)

        self.listbox = tk.Listbox(frame_list, height=10)
        self.listbox.pack(fill="both", expand=True, padx=6, pady=6)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        btns = ttk.Frame(frame_list)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btns, text="↑", width=3, command=self.move_up).pack(side="left")
        ttk.Button(btns, text="↓", width=3, command=self.move_down).pack(side="left")
        ttk.Button(btns, text="Xóa bước", command=self.delete_step).pack(side="left", padx=6)

        # ---- Editor cho 1 bước ----
        frame_edit = ttk.LabelFrame(root, text="Thêm / sửa bước")
        frame_edit.pack(fill="x", padx=10, pady=8)

        ttk.Label(frame_edit, text="Loại hành động:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.action_var = tk.StringVar(value=ACTIONS[0])
        ttk.Combobox(frame_edit, textvariable=self.action_var, values=ACTIONS, state="readonly", width=28)\
            .grid(row=0, column=1, columnspan=2, sticky="w", padx=6, pady=4)

        ttk.Label(frame_edit, text="Vị trí (x, y):").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.pos_label = ttk.Label(frame_edit, text="Chưa đặt")
        self.pos_label.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(frame_edit, text="Lấy vị trí (đợi 3s rồi rê chuột tới đích)",
                   command=self.capture_position).grid(row=1, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(frame_edit, text="Mã / nội dung nhập:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.text_entry = ttk.Entry(frame_edit, width=30)
        self.text_entry.grid(row=2, column=1, columnspan=2, sticky="w", padx=6, pady=4)

        self.enter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame_edit, text="Nhấn Enter sau khi nhập", variable=self.enter_var)\
            .grid(row=3, column=1, sticky="w", padx=6, pady=4)

        self._pending_x = None
        self._pending_y = None

        ttk.Button(frame_edit, text="➕ Thêm bước này vào danh sách", command=self.add_step)\
            .grid(row=4, column=0, columnspan=3, sticky="we", padx=6, pady=8)

        # ---- Cấu hình chạy ----
        frame_run = ttk.LabelFrame(root, text="Chạy vòng lặp")
        frame_run.pack(fill="x", padx=10, pady=8)

        ttk.Label(frame_run, text="Độ trễ giữa các bước (giây):").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.delay_entry = ttk.Entry(frame_run, width=8)
        self.delay_entry.insert(0, "0.4")
        self.delay_entry.grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frame_run, text="Số lần lặp (0 = vô hạn tới khi bấm Dừng):").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.loop_entry = ttk.Entry(frame_run, width=8)
        self.loop_entry.insert(0, "0")
        self.loop_entry.grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Button(frame_run, text="▶ Bắt đầu", command=self.start).grid(row=2, column=0, padx=6, pady=8)
        ttk.Button(frame_run, text="⏹ Dừng", command=self.stop).grid(row=2, column=1, padx=6, pady=8)

        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(root, textvariable=self.status_var, foreground="blue").pack(padx=10, pady=4)

        ttk.Button(root, text="💾 Lưu cấu hình", command=self.save_config).pack(side="left", padx=10, pady=6)
        ttk.Button(root, text="📂 Tải cấu hình", command=self.load_config).pack(side="left", padx=4, pady=6)

        self.refresh_listbox()
        self.load_config(silent=True)

    # ---------- Editor ----------
    def capture_position(self):
        self.status_var.set("Đang đợi 3 giây... rê chuột tới vị trí cần lấy!")
        self.root.update()

        def worker():
            time.sleep(3)
            x, y = pyautogui.position()
            self._pending_x, self._pending_y = x, y
            self.pos_label.config(text=f"({x}, {y})")
            self.status_var.set(f"Đã lấy vị trí ({x}, {y}). Bấm 'Thêm bước này' để lưu.")

        threading.Thread(target=worker, daemon=True).start()

    def add_step(self):
        if self._pending_x is None:
            messagebox.showwarning("Thiếu vị trí", "Hãy bấm 'Lấy vị trí' và rê chuột tới đích trước.")
            return
        step = Step(
            action=self.action_var.get(),
            x=self._pending_x,
            y=self._pending_y,
            text=self.text_entry.get(),
            press_enter=self.enter_var.get(),
        )
        self.steps.append(step)
        self.refresh_listbox()
        # reset editor cho bước tiếp theo
        self._pending_x = None
        self._pending_y = None
        self.pos_label.config(text="Chưa đặt")
        self.text_entry.delete(0, "end")
        self.enter_var.set(False)
        self.status_var.set(f"Đã thêm bước #{len(self.steps)}.")

    def refresh_listbox(self):
        self.listbox.delete(0, "end")
        for i, s in enumerate(self.steps, 1):
            self.listbox.insert("end", f"{i}. {s.label()}")

    def on_select(self, _event):
        pass

    def get_selected_index(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def delete_step(self):
        i = self.get_selected_index()
        if i is None:
            return
        del self.steps[i]
        self.refresh_listbox()

    def move_up(self):
        i = self.get_selected_index()
        if i is None or i == 0:
            return
        self.steps[i - 1], self.steps[i] = self.steps[i], self.steps[i - 1]
        self.refresh_listbox()
        self.listbox.select_set(i - 1)

    def move_down(self):
        i = self.get_selected_index()
        if i is None or i == len(self.steps) - 1:
            return
        self.steps[i + 1], self.steps[i] = self.steps[i], self.steps[i + 1]
        self.refresh_listbox()
        self.listbox.select_set(i + 1)

    # ---------- Lưu / tải cấu hình ----------
    def save_config(self):
        data = [s.to_dict() for s in self.steps]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status_var.set(f"Đã lưu {len(self.steps)} bước vào {CONFIG_FILE}.")
        except Exception as e:
            messagebox.showerror("Lỗi lưu", str(e))

    def load_config(self, silent=False):
        if not os.path.exists(CONFIG_FILE):
            if not silent:
                messagebox.showinfo("Không có file", f"Không tìm thấy {CONFIG_FILE}")
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.steps = [Step.from_dict(d) for d in data]
            self.refresh_listbox()
            self.status_var.set(f"Đã tải {len(self.steps)} bước từ {CONFIG_FILE}.")
        except Exception as e:
            if not silent:
                messagebox.showerror("Lỗi tải", str(e))

    # ---------- Chạy tự động ----------
    def start(self):
        if self.running:
            return
        if not self.steps:
            messagebox.showwarning("Chưa có bước nào", "Hãy thêm ít nhất 1 bước trước khi chạy.")
            return
        try:
            delay = float(self.delay_entry.get())
        except ValueError:
            delay = 0.4
        try:
            loop_count = int(self.loop_entry.get())
        except ValueError:
            loop_count = 0

        self.running = True
        self.stop_flag.clear()
        self.status_var.set("Đang chạy... (bấm nút Dừng, hoặc rê chuột lên góc trên-trái màn hình để dừng khẩn cấp)")
        threading.Thread(target=self.run_loop, args=(delay, loop_count), daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        self.running = False
        self.status_var.set("Đã dừng.")

    def run_loop(self, delay, loop_count):
        count = 0
        try:
            while not self.stop_flag.is_set():
                for step in self.steps:
                    if self.stop_flag.is_set():
                        break
                    self.execute_step(step)
                    time.sleep(delay)
                count += 1
                self.status_var.set(f"Đang chạy... đã lặp {count} lần.")
                if loop_count != 0 and count >= loop_count:
                    break
        except pyautogui.FailSafeException:
            self.status_var.set("Đã dừng khẩn cấp (chuột ở góc màn hình).")
        finally:
            self.running = False
            if not self.stop_flag.is_set():
                self.status_var.set(f"Hoàn tất {count} lần lặp.")

    def execute_step(self, step: Step):
        pyautogui.click(step.x, step.y)
        if step.action == "Click + Copy (Ctrl+C)":
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.1)
        elif step.action == "Click + Paste (Ctrl+V)":
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
        elif step.action == "Click + Nhập mã":
            if step.text:
                time.sleep(0.1)
                # dùng clipboard + paste để gõ được cả tiếng Việt có dấu, ký tự đặc biệt
                pyperclip.copy(step.text)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.1)
            if step.press_enter:
                pyautogui.press("enter")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
