# -*- coding: utf-8 -*-
"""
CPU 使用率趋势分析平台（NiceGUI 3.16 + SQLite + ECharts）
运行：python cpu_web.py
访问：http://<服务器IP>:8080
"""
import csv
import sqlite3
from nicegui import ui

CSV_FILE = "cpu.csv"
DB_FILE = "cpu_stats.db"
ALL = "全部日期"
THRESHOLD = 80
METRICS = {"Avg(%)": "avg_pct", "Max(%)": "max_pct", "Min(%)": "min_pct"}

COLS = [
    {"name": "status", "label": "状态", "field": "status", "align": "center"},
    {"name": "ip", "label": "节点IP", "field": "ip", "sortable": True},
    {"name": "cnt", "label": "区间数", "field": "cnt", "sortable": True, "align": "right"},
    {"name": "avg", "label": "总均值(%)", "field": "avg", "sortable": True, "align": "right"},
    {"name": "peak", "label": "峰值(%)", "field": "peak", "sortable": True, "align": "right"},
    {"name": "peak_time", "label": "峰值时间", "field": "peak_time"},
    {"name": "floor", "label": "最低(%)", "field": "floor", "sortable": True, "align": "right"},
]

SUMMARY_SQL = """
SELECT ip, COUNT(*) AS cnt, ROUND(AVG(avg_pct),2) AS avg, MAX(max_pct) AS peak,
       (SELECT max_time FROM cpu_stats c2 WHERE c2.ip=c1.ip ORDER BY max_pct DESC LIMIT 1) AS peak_time,
       MIN(min_pct) AS floor
FROM cpu_stats c1 GROUP BY ip ORDER BY avg DESC
"""

# ==================== 数据层 ====================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cpu_stats(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT, date TEXT, time_range TEXT,
                max_pct REAL, max_time TEXT, min_pct REAL, min_time TEXT,
                avg_pct REAL, samples INTEGER, start_ts TEXT,
                UNIQUE(ip, date, time_range)
            );
            CREATE INDEX IF NOT EXISTS idx_ip_ts ON cpu_stats(ip, start_ts);
        """)

def import_csv(path=CSV_FILE):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            start = r["Time Range"].split("-")[0]
            rows.append((
                r["Node IP"], r["Date"], r["Time Range"],
                float(r["Max(%)"]), r["Max Time"],
                float(r["Min(%)"]), r["Min Time"],
                float(r["Avg(%)"]), int(r["Samples"]),
                f"{r['Date']} {start}"
            ))
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO cpu_stats "
            "(ip,date,time_range,max_pct,max_time,min_pct,min_time,avg_pct,samples,start_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
    return len(rows)

def query(sql, params=()):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]

# ==================== ECharts 配置构建 ====================
def build_chart_options(metric, date, ips):
    col = METRICS[metric]
    series, legend = [], []
    for ip in ips:
        sql = f"SELECT start_ts, {col} AS val FROM cpu_stats WHERE ip=?"
        params = [ip]
        if date != ALL:
            sql += " AND date=?"
            params.append(date)
        rows = query(sql + " ORDER BY start_ts", params)
        if not rows:
            continue
        legend.append(ip)
        s = {
            "name": ip, "type": "line", "smooth": True,
            "showSymbol": False, "emphasis": {"focus": "series"},
            "data": [[r["start_ts"], r["val"]] for r in rows]
        }
        if not series:
            s["markLine"] = {
                "silent": True, "symbol": "none",
                "lineStyle": {"color": "#e53935", "type": "dashed"},
                "label": {"formatter": f"告警 {THRESHOLD}%"},
                "data": [{"yAxis": THRESHOLD}]
            }
        series.append(s)
    return {
        "title": {"text": f"CPU 趋势（{metric}）· {date}", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend, "type": "scroll", "bottom": 0},
        "toolbox": {"feature": {"dataZoom": {}, "restore": {}, "saveAsImage": {}}},
        "grid": {"left": 50, "right": 30, "top": 50, "bottom": 90},
        "dataZoom": [{"type": "inside"}, {"type": "slider", "bottom": 30, "height": 20}],
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "max": 100, "name": "CPU (%)"},
        "series": series,
    }

# ==================== 页面 ====================
@ui.page("/")
def index():
    chart = None
    table = None
    stat_nodes = stat_records = stat_peak = stat_hot = None
    metric_sel = date_sel = ip_sel = None

    def refresh(_=None):
        # ✅ NiceGUI 3.16 正确方式：清空 .options 再更新内容，最后 .update()
        new_opts = build_chart_options(
            metric_sel.value, date_sel.value, ip_sel.value or []
        )
        chart.options.clear()
        chart.options.update(new_opts)
        chart.update()

        rows = query(SUMMARY_SQL)
        for r in rows:
            r["status"] = "🔴" if r["peak"] >= THRESHOLD else "🟢"
        table.rows = rows
        table.update()

        s = query("SELECT COUNT(DISTINCT ip) n, COUNT(*) c, "
                  "IFNULL(MAX(max_pct),0) p FROM cpu_stats")[0]
        hot = query("SELECT COUNT(DISTINCT ip) n FROM cpu_stats "
                    "WHERE max_pct>=?", (THRESHOLD,))[0]["n"]
        stat_nodes.text = f"节点数：{s['n']}"
        stat_records.text = f"记录数：{s['c']}"
        stat_peak.text = f"全局峰值：{s['p']}%"
        stat_hot.text = f"{'🔴' if hot else '🟢'} 高危节点：{hot}"

    def load_filters():
        dates = [r["date"] for r in
                 query("SELECT DISTINCT date FROM cpu_stats ORDER BY date")]
        date_sel.options = [ALL] + dates
        date_sel.value = ALL
        ips = [r["ip"] for r in
               query("SELECT DISTINCT ip FROM cpu_stats ORDER BY ip")]
        ip_sel.options = ips
        ip_sel.value = ips

    def reimport():
        try:
            n = import_csv()
            ui.notify(f"✅ 导入 {n} 条记录", type="positive")
        except FileNotFoundError:
            ui.notify(f"❌ 未找到 {CSV_FILE}", type="negative")
            return
        load_filters()
        refresh()

    # ========== 布局 ==========
    with ui.header().classes("bg-blue-700 items-center"):
        ui.label("📊 CPU 使用率趋势分析平台").classes("text-h6")
        ui.space()
        ui.dark_mode()
        ui.button(icon="sync", on_click=reimport).props("flat").tooltip("重新导入 csv")

    with ui.row().classes("w-full p-4 gap-4 items-start"):
        with ui.column().classes("w-72 gap-4 shrink-0"):
            with ui.card().classes("w-full"):
                ui.label("过滤条件").classes("text-subtitle1 q-mb-sm")
                metric_sel = ui.select(list(METRICS), value="Avg(%)", label="指标").classes("w-full")
                date_sel = ui.select([ALL], value=ALL, label="日期").classes("w-full")
                ip_sel = ui.select([], multiple=True, label="节点IP（多选）").classes("w-full")
                with ui.row().classes("w-full justify-between q-mt-sm"):
                    ui.button("全选", on_click=lambda: (ip_sel.set_value(ip_sel.options), refresh())).props("dense").classes("no-shadow")
                    ui.button("清空", on_click=lambda: (ip_sel.set_value([]), refresh())).props("dense outline").classes("no-shadow")
            with ui.card().classes("w-full"):
                ui.label("数据概览").classes("text-subtitle1 q-mb-sm")
                stat_nodes = ui.label("节点数：-")
                stat_records = ui.label("记录数：-")
                stat_peak = ui.label("全局峰值：-")
                stat_hot = ui.label("高危节点：-")

        with ui.column().classes("flex-1 gap-4"):
            with ui.card().classes("w-full"):
                chart = ui.echart({}).classes("w-full h-[460px]")
            with ui.card().classes("w-full"):
                ui.label("节点汇总").classes("text-subtitle1 q-mb-sm")
                table = ui.table(columns=COLS, rows=[], pagination={"rowsPerPage": 10}) \
                    .classes("w-full").props("dense")
                table.props(':row-class="row => row.peak >= 80 ? \'bg-red-1\' : \'\'"')

    metric_sel.on_value_change(refresh)
    date_sel.on_value_change(refresh)
    ip_sel.on_value_change(refresh)
    load_filters()
    refresh()


if __name__ in {"__main__", "__mp_main__"}:
    init_db()
    try:
        print(f"导入 {import_csv()} 条记录")
    except FileNotFoundError:
        print(f"未找到 {CSV_FILE}，使用已有数据库")
    ui.run(host="0.0.0.0", port=8080, title="CPU 趋势分析", reload=False)