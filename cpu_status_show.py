# -*- coding: utf-8 -*-
import csv
import sqlite3
from datetime import datetime

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

CSV_FILE = "cpu.csv"
DB_FILE = "cpu_stats.db"
ALL = "全部日期"
METRICS = {"Avg(%)": "avg_pct", "Max(%)": "max_pct", "Min(%)": "min_pct"}

# 让 matplotlib 支持中文（按操作系统自动选字体）
import matplotlib.font_manager as fm
for _f in ("Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", "WenQuanYi Micro Hei"):
    if any(_f.lower() in n.name.lower() for n in fm.fontManager.ttflist):
        matplotlib.rcParams["font.sans-serif"] = [_f]
        break
matplotlib.rcParams["axes.unicode_minus"] = False

# ================= 1. SQLite 层 =================
def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cpu_stats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL, date TEXT NOT NULL, time_range TEXT NOT NULL,
            max_pct REAL, max_time TEXT, min_pct REAL, min_time TEXT,
            avg_pct REAL, samples INTEGER, start_ts TEXT,
            UNIQUE(ip, date, time_range)          -- 防止重复导入
        );
        CREATE INDEX IF NOT EXISTS idx_ip_ts ON cpu_stats(ip, start_ts);
    """)

def import_csv(conn, path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            start = r["Time Range"].split("-")[0]          # "00:00-09:00" -> "00:00"
            rows.append((r["Node IP"], r["Date"], r["Time Range"],
                         float(r["Max(%)"]), r["Max Time"],
                         float(r["Min(%)"]), r["Min Time"],
                         float(r["Avg(%)"]), int(r["Samples"]),
                         f"{r['Date']} {start}"))           # 可排序的时间戳
    conn.executemany("""INSERT OR REPLACE INTO cpu_stats
        (ip,date,time_range,max_pct,max_time,min_pct,min_time,avg_pct,samples,start_ts)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    return len(rows)

# ================= 2. GUI 层 =================
class App:
    def __init__(self, conn):
        self.conn = conn
        self.win = ttk.Window(themename="flatly", title="CPU 使用率趋势分析",
                              size=(1360, 800))
        self._build()
        self._load_filters()
        self.redraw()
        self.win.mainloop()

    def _build(self):
        # ---------- 左侧控制区 ----------
        left = ttk.Frame(self.win, padding=10, width=250)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ttk.Label(left, text="指标", font=("", 11, "bold")).pack(anchor="w")
        self.metric_var = ttk.StringVar(value="Avg(%)")
        cb = ttk.Combobox(left, textvariable=self.metric_var,
                          values=list(METRICS), state="readonly")
        cb.pack(fill="x", pady=(0, 10))
        cb.bind("<<ComboboxSelected>>", lambda e: self.redraw())

        ttk.Label(left, text="日期", font=("", 11, "bold")).pack(anchor="w")
        self.date_var = ttk.StringVar(value=ALL)
        self.date_cb = ttk.Combobox(left, textvariable=self.date_var, state="readonly")
        self.date_cb.pack(fill="x", pady=(0, 10))
        self.date_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw())

        ttk.Label(left, text="节点IP（Ctrl/Shift多选）",
                  font=("", 11, "bold")).pack(anchor="w")
        self.ip_list = tk.Listbox(left, selectmode="extended", exportselection=False)
        self.ip_list.pack(fill="both", expand=True)

        bf = ttk.Frame(left)
        bf.pack(fill="x", pady=5)
        ttk.Button(bf, text="全选", bootstyle="success",
                   command=self.select_all).pack(side="left", expand=True, fill="x")
        ttk.Button(bf, text="刷新", bootstyle="info",
                   command=self.redraw).pack(side="left", expand=True, fill="x")

        # ---------- 右侧：趋势图 + 汇总表 ----------
        right = ttk.Frame(self.win, padding=(0, 10, 10, 10))
        right.pack(side="left", fill="both", expand=True)

        self.fig = Figure(figsize=(10, 5.2), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, right).update()   # 缩放/平移/保存工具条
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)
        self.annot = self.ax.annotate("", xy=(0, 0), xytext=(12, 12),
                    textcoords="offset points", fontsize=9, visible=False,
                    bbox=dict(boxstyle="round,pad=.4", fc="#fff3cd", alpha=.9))

        cols = ("ip", "cnt", "avg", "peak", "peak_time", "floor")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=7)
        for c, t, wdt in zip(cols,
                ("节点IP", "区间数", "总均值(%)", "峰值(%)", "峰值时间", "最低(%)"),
                (130, 60, 90, 70, 160, 70)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wdt, anchor="center")
        self.tree.pack(fill="x", pady=(8, 0))
        self.tree.tag_configure("hot", background="#f8d7da", foreground="#842026")
        self.tree.bind("<Double-1>", self.focus_ip)

    # ---------- 数据加载 ----------
    def _load_filters(self):
        dates = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT date FROM cpu_stats ORDER BY date")]
        self.date_cb["values"] = [ALL] + dates
        self.ip_list.delete(0, "end")
        for ip in [r[0] for r in self.conn.execute(
                "SELECT DISTINCT ip FROM cpu_stats ORDER BY ip")]:
            self.ip_list.insert("end", ip)
        self.ip_list.selection_set(0, "end")     # 默认全选 => 同图展示所有IP

    def select_all(self):
        self.ip_list.selection_set(0, "end")
        self.redraw()

    # ---------- 核心：同一张图画所有IP ----------
    def redraw(self):
        self.ax.clear()
        self.lines = []
        col = METRICS[self.metric_var.get()]
        d = self.date_var.get()

        for ip in [self.ip_list.get(i) for i in self.ip_list.curselection()]:
            sql, params = f"SELECT start_ts, {col} FROM cpu_stats WHERE ip=?", [ip]
            if d != ALL:
                sql += " AND date=?"
                params.append(d)
            rows = self.conn.execute(sql + " ORDER BY start_ts", params).fetchall()
            if not rows:
                continue
            xs = [datetime.strptime(r[0], "%Y-%m-%d %H:%M") for r in rows]
            ys = [r[1] for r in rows]
            line, = self.ax.plot(xs, ys, marker="o", ms=3, lw=1.4, label=ip)
            line.set_pickradius(6)               # 悬停拾取半径
            line._meta = rows
            self.lines.append(line)

        self.ax.axhline(80, color="red", ls="--", lw=1, alpha=.6)
        self.ax.set_ylim(0, 100)
        self.ax.set_ylabel("CPU 使用率 (%)")
        self.ax.set_title(f"CPU 趋势（{self.metric_var.get()}）  日期：{d}")
        self.ax.grid(alpha=.3)
        self.ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%H:%M" if d != ALL else "%m-%d %H:%M"))
        self.fig.autofmt_xdate()
        self.ax.legend(loc="center left", bbox_to_anchor=(1.0, .5), fontsize=9)
        self.fig.tight_layout()
        self.canvas.draw()
        self.refresh_summary()

    # ---------- 悬停提示 ----------
    def on_hover(self, event):
        if event.inaxes != self.ax:
            self.annot.set_visible(False)
            self.canvas.draw_idle()
            return
        for line in self.lines:
            hit, ind = line.contains(event)
            if hit:
                ts, val = line._meta[ind["ind"][0]]
                self.annot.xy = (mdates.date2num(
                    datetime.strptime(ts, "%Y-%m-%d %H:%M")), val)
                self.annot.set_text(f"{line.get_label()}\n{ts}\n{val}%")
                self.annot.set_visible(True)
                self.canvas.draw_idle()
                return
        if self.annot.get_visible():
            self.annot.set_visible(False)
            self.canvas.draw_idle()

    # ---------- 汇总表（SQL 聚合） ----------
    def refresh_summary(self):
        self.tree.delete(*self.tree.get_children())
        sql = """SELECT ip, COUNT(*), ROUND(AVG(avg_pct),2), MAX(max_pct),
                    (SELECT max_time FROM cpu_stats c2
                      WHERE c2.ip = c1.ip ORDER BY max_pct DESC LIMIT 1),
                    MIN(min_pct)
                 FROM cpu_stats c1 GROUP BY ip ORDER BY 3 DESC"""
        for row in self.conn.execute(sql):
            tag = "hot" if row[3] >= 80 else ""    # 峰值>=80% 标红
            self.tree.insert("", "end", values=row, tags=(tag,))

    # ---------- 双击汇总表：单独聚焦某IP ----------
    def focus_ip(self, e):
        sel = self.tree.selection()
        if not sel:
            return
        ip = str(self.tree.item(sel[0])["values"][0])
        ips = list(self.ip_list.get(0, "end"))
        if ip in ips:
            self.ip_list.selection_clear(0, "end")
            self.ip_list.selection_set(ips.index(ip))
            self.redraw()

def main():
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    try:
        print(f"导入 {import_csv(conn, CSV_FILE)} 条记录")
    except FileNotFoundError:
        print(f"未找到 {CSV_FILE}，使用已有数据库")
    App(conn)

if __name__ == "__main__":
    main()
