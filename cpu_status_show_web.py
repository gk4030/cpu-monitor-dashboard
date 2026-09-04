import os
import csv
import io
import json
import sqlite3
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# ==================== 全局配置 ====================
DB_FILE = "cpu_stats.db"
CONFIG_FILE = "llm_config.json"
THRESHOLD = 80.0
HIGH_LOAD = 60.0
LOW_LOAD = 20.0
ALL = "全部"

DEFAULT_LLM_CONFIG = {
    "enabled": False,
    "api_url": "https://api.openai.com/v1/chat/completions",
    "api_key": "",
    "model": "gpt-3.5-turbo",
    "timeout": 30
}

# 常用 API 与模型预设映射
API_PRESETS = {
    "OpenAI": {
        "url": "https://api.openai.com/v1/chat/completions",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
    },
    "DeepSeek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "models": ["deepseek-chat", "deepseek-reasoner"]
    },
    "SiliconFlow (硅基流动)": {
        "url": "https://api.siliconflow.cn/v1/chat/completions",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "THUDM/glm-4-9b-chat"]
    },
    "ZhipuAI (智谱清言)": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air"]
    },
    "Moonshot (月之暗面)": {
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]
    },
    "Ollama (本地部署)": {
        "url": "http://localhost:11434/v1/chat/completions",
        "models": ["qwen2:7b", "llama3:8b", "mistral:7b"]
    },
    "自定义 / Custom": {
        "url": "",
        "models": []
    }
}

# ==================== 配置持久化 ====================
def load_llm_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    save_llm_config(DEFAULT_LLM_CONFIG)
    return DEFAULT_LLM_CONFIG

def save_llm_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# ==================== 数据库层 ====================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def query(sql, params=()):
    with get_db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cpu_stats(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT, timestamp TEXT, cpu_pct REAL, date TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ip_ts ON cpu_stats(ip, timestamp);
            CREATE INDEX IF NOT EXISTS idx_date ON cpu_stats(date);
        """)

def parse_and_import(file_stream):
    rows, errors = [], []
    try:
        content = file_stream.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        file_stream.seek(0)
        content = file_stream.read().decode('gbk', errors='ignore')
    
    reader = csv.DictReader(io.StringIO(content))
    for line_no, raw in enumerate(reader, start=2):
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        try:
            ts = r["Timestamp"]
            cpu = float(r["CPU Usage(%)"])
            ip = r["Node IP"]
            date_part = ts.split(" ")[0] if " " in ts else ts[:10]
            rows.append((ip, ts, cpu, date_part))
        except KeyError as e:
            errors.append(f"第{line_no}行: 缺少列 {e}")
        except ValueError as e:
            errors.append(f"第{line_no}行: 数据格式错误 ({e})")
    
    if not rows:
        return 0, "未解析到有效数据。请检查CSV表头是否包含: Node IP, Timestamp, CPU Usage(%)"
    
    with get_db() as conn:
        conn.executemany("INSERT INTO cpu_stats (ip, timestamp, cpu_pct, date) VALUES (?,?,?,?)", rows)
    
    warn = f" (⚠️ {len(errors)}行异常已跳过)" if errors else ""
    return len(rows), warn

# ==================== LLM 诊断引擎 ====================
def llm_diagnose(stats):
    config = load_llm_config()
    if not config.get("enabled") or not config.get("api_key"):
        return None

    prompt = f"""你是一位资深SRE运维专家。请根据以下CPU监控统计数据，用中文给出专业、简洁的诊断分析和建议。

## 监控数据摘要
- 分析目标: {stats['target']}
- 时间范围: {stats['date']}
- 采样点总数: {stats['samples']}
- 平均CPU使用率: {stats['avg']}%
- 峰值/谷值: {stats['max']}% / {stats['min']}%
- 高负载占比(≥{THRESHOLD}%): {stats['high_pct']}%
- 空闲占比(<{LOW_LOAD}%): {stats['idle_pct']}%

## 输出要求
请使用 Markdown 格式输出，包含以下部分：
1. **整体评估**：一句话总结当前CPU健康状态
2. **关键发现**：列出2-3个最值得关注的点（使用列表）
3. **行动建议**：给出具体可执行的运维建议
4. **风险提示**：预判可能发生的潜在问题
"""

    try:
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": "你是一位资深SRE运维专家，擅长分析服务器监控数据并给出专业建议。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 800,
            "temperature": 0.7
        }
        
        resp = requests.post(
            config["api_url"], headers=headers, json=payload,
            timeout=config.get("timeout", 30)
        )
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️ LLM 调用失败: {e}")
        return None

# ==================== 智能分析引擎 ====================
def analyze_cpu(date, selected_ip):
    sql = "SELECT cpu_pct FROM cpu_stats WHERE 1=1"
    params = []
    if selected_ip != ALL: sql += " AND ip=?"; params.append(selected_ip)
    if date != ALL: sql += " AND date=?"; params.append(date)
    
    rows = query(sql, params)
    if not rows:
        return {"empty": True}
    
    values = [r["cpu_pct"] for r in rows]
    total = len(values)
    avg_val = sum(values) / total
    max_val, min_val = max(values), min(values)
    
    high_count = sum(1 for v in values if v >= THRESHOLD)
    idle_count = sum(1 for v in values if v < LOW_LOAD)
    high_pct = round(high_count / total * 100, 1)
    idle_pct = round(idle_count / total * 100, 1)
    
    suggestions = []
    risk_level, risk_color = "🟢 健康", "#67C23A"
    if high_pct >= 30: risk_level, risk_color = "🔴 高风险", "#F56C6C"
    elif high_pct >= 10: risk_level, risk_color = "🟡 中风险", "#E6A23C"
    
    if high_pct >= 10: suggestions.append(f"⚠️ {high_pct}% 的采样点超过 {THRESHOLD}% 告警线，建议排查高CPU进程或扩容")
    if idle_pct >= 30: suggestions.append(f"💤 空闲率 {idle_pct}%，资源利用率偏低，可评估缩容")
    if avg_val > HIGH_LOAD: suggestions.append(f"📈 平均CPU {avg_val:.1f}%，整体负载偏高")
    if max_val - min_val > 60: suggestions.append(f"🔄 CPU波动幅度达 {max_val-min_val:.1f}%，负载不稳定")
    if not suggestions: suggestions.append("✅ 各项指标正常，无需特别处理")
    
    base_stats = {
        "empty": False,
        "target": selected_ip if selected_ip != ALL else "所有节点",
        "date": date if date != ALL else "全部时段",
        "samples": total, "avg": round(avg_val, 1), "max": round(max_val, 1), "min": round(min_val, 1),
        "high_pct": high_pct, "idle_pct": idle_pct,
        "risk_level": risk_level, "risk_color": risk_color,
        "suggestions": suggestions,
        "use_llm": False, "llm_report": None
    }
    
    llm_text = llm_diagnose(base_stats)
    if llm_text:
        base_stats["use_llm"] = True
        base_stats["llm_report"] = llm_text
        
    return base_stats

# ==================== ECharts 配置 ====================
def build_chart_options(date, selected_ip):
    series, legend = [], []
    ip_list = [r["ip"] for r in query("SELECT DISTINCT ip FROM cpu_stats ORDER BY ip")] if selected_ip == ALL else [selected_ip]
    
    for ip in ip_list:
        sql, params = "SELECT timestamp, cpu_pct FROM cpu_stats WHERE ip=?", [ip]
        if date != ALL: sql += " AND date=?"; params.append(date)
        rows = query(sql + " ORDER BY timestamp ASC", params)
        if not rows: continue
        legend.append(ip)
        s = {"name": ip, "type": "line", "showSymbol": False, "sampling": "lttb",
             "data": [[r["timestamp"], r["cpu_pct"]] for r in rows]}
        if not series:
            s["markLine"] = {"silent": True, "symbol": "none", "lineStyle": {"color": "#e53935", "type": "dashed", "width": 2},
                             "label": {"formatter": f"告警线 {THRESHOLD}%", "position": "end"}, "data": [{"yAxis": THRESHOLD}]}
        series.append(s)
    
    return {
        "title": {"text": f"CPU 实时趋势分析 · {date}", "left": "center", "textStyle": {"fontSize": 16}},
        "tooltip": {"trigger": "axis", "formatter": "{b}<br/>CPU使用率: {c}%"} if selected_ip != ALL else {"trigger": "axis"},
        "legend": {"data": legend, "type": "scroll", "bottom": 0, "pageIconSize": 12},
        "toolbox": {"feature": {"dataZoom": {"yAxisIndex": "none"}, "restore": {}, "saveAsImage": {}}},
        "grid": {"left": 60, "right": 40, "top": 60, "bottom": 100},
        "dataZoom": [{"type": "inside", "start": 80, "end": 100}, {"type": "slider", "bottom": 40, "height": 20, "start": 80, "end": 100}],
        "xAxis": {"type": "category", "boundaryGap": False, "axisLabel": {"rotate": 45, "fontSize": 10}},
        "yAxis": {"type": "value", "max": 100, "name": "CPU Usage (%)"},
        "series": series,
    }

# ==================== HTML 模板 ====================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>CPU 监控大盘</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f5f7fa; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        .filters { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
        select, input[type="text"], input[type="password"], input[type="number"] { padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; min-width: 150px; }
        .upload-area { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: #fff; border: 1px dashed #409EFF; border-radius: 4px; }
        .upload-area input[type="file"] { font-size: 12px; max-width: 200px; }
        .btn { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; color: #fff; transition: opacity 0.2s; }
        .btn:hover { opacity: 0.85; }
        .btn-primary { background: #409EFF; }
        .btn-success { background: #67C23A; }
        .btn-danger { background: #F56C6C; }
        .btn-warning { background: #E6A23C; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        #chart { width: 100%; height: 600px; background: #fff; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); }
        .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-top: 20px; }
        .card { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); text-align: center; }
        .card h3 { margin: 0 0 10px 0; color: #666; font-size: 14px; }
        .card p { margin: 0; font-size: 24px; font-weight: bold; color: #333; }
        .alert { color: #e53935 !important; }
        .analysis-panel { margin-top: 20px; background: #fff; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); overflow: hidden; }
        .analysis-header { padding: 16px 24px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
        .analysis-header h3 { margin: 0; font-size: 16px; color: #333; }
        .risk-badge { padding: 4px 12px; border-radius: 12px; font-size: 13px; font-weight: bold; color: #fff; }
        .analysis-body { padding: 20px 24px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
        .analysis-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
        .stat-item { background: #f9fafb; padding: 12px; border-radius: 6px; text-align: center; }
        .stat-item .label { font-size: 12px; color: #999; margin-bottom: 4px; }
        .stat-item .value { font-size: 18px; font-weight: bold; color: #333; }
        .analysis-suggestions h4 { margin: 0 0 12px 0; font-size: 14px; color: #666; }
        .suggestion-list { list-style: none; padding: 0; margin: 0; }
        .suggestion-list li { padding: 8px 12px; margin-bottom: 8px; background: #f0f7ff; border-left: 3px solid #409EFF; border-radius: 0 4px 4px 0; font-size: 13px; color: #333; line-height: 1.6; }
        .analysis-empty, .analysis-loading { padding: 40px; text-align: center; color: #999; font-size: 14px; }
        .analysis-loading { color: #409EFF; font-weight: bold; }
        .llm-report { padding: 20px 24px; border-bottom: 1px solid #eee; }
        .llm-report h4 { margin: 0 0 12px 0; font-size: 15px; color: #333; display: flex; align-items: center; gap: 8px; }
        .llm-text { background: linear-gradient(135deg, #f5f7fa 0%, #e8f4f8 100%); padding: 16px 20px; border-radius: 8px; border: 1px solid #e1f0f5; font-size: 14px; line-height: 1.8; color: #333; }
        .llm-text h1, .llm-text h2, .llm-text h3 { margin-top: 12px; margin-bottom: 8px; }
        .llm-text ul { padding-left: 20px; }
        .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; justify-content: center; align-items: center; z-index: 1000; }
        .modal { background: #fff; padding: 24px; border-radius: 8px; width: 500px; max-width: 90%; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }
        .modal h3 { margin: 0 0 20px 0; font-size: 18px; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; font-size: 13px; color: #666; font-weight: bold; }
        .form-group input, .form-group select { width: 100%; box-sizing: border-box; }
        .form-group .hint { font-size: 12px; color: #999; margin-top: 4px; }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
        .switch { position: relative; display: inline-block; width: 40px; height: 20px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 20px; }
        .slider:before { position: absolute; content: ""; height: 14px; width: 14px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background-color: #67C23A; }
        input:checked + .slider:before { transform: translateX(20px); }
        .toast { position: fixed; top: 20px; right: 20px; padding: 12px 24px; border-radius: 6px; color: #fff; font-size: 14px; z-index: 9999; display: none; animation: fadeIn 0.3s; }
        .toast.success { background: #67C23A; }
        .toast.error { background: #F56C6C; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
        @media (max-width: 768px) { .analysis-body { grid-template-columns: 1fr; } .summary { grid-template-columns: repeat(2, 1fr); } }
    </style>
</head>
<body>
    <div class="header">
        <h2>🖥️ 节点 CPU 监控大盘</h2>
        <div class="filters">
            <div class="upload-area">
                <input type="file" id="csvFile" accept=".csv">
                <button class="btn btn-success" id="uploadBtn" onclick="uploadCSV()">📂 导入CSV</button>
            </div>
            <select id="dateSel" onchange="updateAll()">
                <option value="全部">所有日期</option>
                {% for d in dates %}<option value="{{d}}">{{d}}</option>{% endfor %}
            </select>
            <select id="ipSel" onchange="updateAll()">
                <option value="全部">所有节点IP</option>
                {% for ip in ips %}<option value="{{ip}}">{{ip}}</option>{% endfor %}
            </select>
            <button class="btn btn-primary" onclick="updateAll()">🔄 刷新</button>
            <button class="btn btn-danger" onclick="clearData()">🗑️ 清空</button>
            <button class="btn btn-warning" onclick="openConfigModal()">⚙️ AI设置</button>
        </div>
    </div>

    <div id="chart"></div>

    <div class="summary">
        <div class="card"><h3>监控节点数</h3><p id="statNodes">-</p></div>
        <div class="card"><h3>采样记录总数</h3><p id="statRecords">-</p></div>
        <div class="card"><h3>历史最高 CPU</h3><p id="statPeak" class="alert">-</p></div>
        <div class="card"><h3>超阈值节点数</h3><p id="statHot" class="alert">-</p></div>
    </div>

    <div class="analysis-panel">
        <div class="analysis-header">
            <h3>📋 智能诊断报告</h3>
            <span class="risk-badge" id="riskBadge" style="display:none;"></span>
        </div>
        <div id="analysisContent">
            <div class="analysis-empty">💡 请先导入CSV数据，或选择IP和日期后查看分析结果</div>
        </div>
    </div>

    <!-- AI 配置弹窗 -->
    <div class="modal-overlay" id="configModal">
        <div class="modal">
            <h3>⚙️ AI 大模型配置</h3>
            <div class="form-group">
                <label>启用 AI 诊断</label>
                <label class="switch"><input type="checkbox" id="cfgEnabled"><span class="slider"></span></label>
            </div>
            <div class="form-group">
                <label>🏢 服务商 / 平台</label>
                <select id="cfgPreset" onchange="onPresetChange()"></select>
                <div class="hint">选择后自动填充API地址和模型列表，也可手动修改</div>
            </div>
            <div class="form-group">
                <label>API 地址 (Base URL)</label>
                <input type="text" id="cfgUrl" placeholder="https://api.openai.com/v1/chat/completions">
            </div>
            <div class="form-group">
                <label>API Key</label>
                <input type="password" id="cfgKey" placeholder="sk-...">
                <div class="hint">Ollama 本地部署可填写任意字符</div>
            </div>
            <div class="form-group">
                <label>模型名称</label>
                <div style="display:flex; gap:8px;">
                    <select id="cfgModelSelect" onchange="onModelSelectChange()" style="flex:1;">
                        <option value="">-- 请先选择服务商 --</option>
                    </select>
                    <input type="text" id="cfgModel" placeholder="或手动输入模型名" style="flex:1;">
                </div>
                <div class="hint">从下拉框选择或直接在右侧输入框中自定义</div>
            </div>
            <div class="form-group">
                <label>超时时间 (秒)</label>
                <input type="number" id="cfgTimeout" value="30" min="5" max="120">
            </div>
            <div class="modal-actions">
                <button class="btn btn-danger" onclick="closeConfigModal()">取消</button>
                <button class="btn btn-primary" onclick="saveConfig()">💾 保存配置</button>
            </div>
        </div>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        const chart = echarts.init(document.getElementById('chart'));
        let presetsCache = {};

        function showToast(msg, type='success') {
            const t = document.getElementById('toast');
            t.textContent = msg; t.className = `toast ${type}`; t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3000);
        }

        async function openConfigModal() {
            const res = await fetch('/api/config');
            const data = await res.json();
            const cfg = data.config;
            presetsCache = data.presets;

            const presetSel = document.getElementById('cfgPreset');
            presetSel.innerHTML = '';
            Object.keys(presetsCache).forEach(name => {
                const opt = document.createElement('option');
                opt.value = name; opt.textContent = name;
                presetSel.appendChild(opt);
            });

            document.getElementById('cfgEnabled').checked = cfg.enabled;
            document.getElementById('cfgUrl').value = cfg.api_url;
            document.getElementById('cfgKey').value = cfg.api_key;
            document.getElementById('cfgModel').value = cfg.model;
            document.getElementById('cfgTimeout').value = cfg.timeout;

            let matchedPreset = '自定义 / Custom';
            for (const [name, p] of Object.entries(presetsCache)) {
                try {
                    if (p.url && cfg.api_url && cfg.api_url.includes(new URL(p.url).hostname)) {
                        matchedPreset = name; break;
                    }
                } catch(e) {}
            }
            presetSel.value = matchedPreset;
            onPresetChange(cfg.model);
            document.getElementById('configModal').style.display = 'flex';
        }

        function onPresetChange(currentModel) {
            const presetName = document.getElementById('cfgPreset').value;
            const preset = presetsCache[presetName];
            const modelSel = document.getElementById('cfgModelSelect');
            const modelInput = document.getElementById('cfgModel');

            if (presetName !== '自定义 / Custom' && preset.url) {
                document.getElementById('cfgUrl').value = preset.url;
            }

            modelSel.innerHTML = '';
            if (preset.models && preset.models.length > 0) {
                preset.models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m; opt.textContent = m;
                    modelSel.appendChild(opt);
                });
                if (currentModel && preset.models.includes(currentModel)) {
                    modelSel.value = currentModel;
                    modelInput.value = currentModel;
                } else {
                    modelSel.selectedIndex = 0;
                    modelInput.value = preset.models[0];
                }
            } else {
                modelSel.innerHTML = '<option value="">-- 请手动输入模型名 --</option>';
                if (!currentModel) modelInput.value = '';
            }
        }

        function onModelSelectChange() {
            const val = document.getElementById('cfgModelSelect').value;
            if (val) document.getElementById('cfgModel').value = val;
        }

        function closeConfigModal() { document.getElementById('configModal').style.display = 'none'; }

        async function saveConfig() {
            const cfg = {
                enabled: document.getElementById('cfgEnabled').checked,
                api_url: document.getElementById('cfgUrl').value.trim(),
                api_key: document.getElementById('cfgKey').value.trim(),
                model: document.getElementById('cfgModel').value.trim(),
                timeout: parseInt(document.getElementById('cfgTimeout').value) || 30
            };
            if (!cfg.model) { showToast('❌ 请填写模型名称', 'error'); return; }
            const res = await fetch('/api/config', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(cfg)
            });
            const data = await res.json();
            if (data.status === 'ok') { showToast('✅ 配置已保存'); closeConfigModal(); updateAll(); }
            else { showToast('❌ 保存失败', 'error'); }
        }

        async function uploadCSV() {
            const fileInput = document.getElementById('csvFile');
            const btn = document.getElementById('uploadBtn');
            if (!fileInput.files.length) { showToast('请先选择CSV文件', 'error'); return; }
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            btn.disabled = true; btn.textContent = '⏳ 导入中...';
            try {
                const res = await fetch('/api/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast(`✅ 成功导入 ${data.imported} 条记录${data.warn || ''}`);
                    fileInput.value = '';
                    setTimeout(() => location.reload(), 1000);
                } else { showToast(`❌ 导入失败: ${data.error}`, 'error'); }
            } catch (e) { showToast(`❌ 网络错误: ${e.message}`, 'error'); }
            finally { btn.disabled = false; btn.textContent = '📂 导入CSV'; }
        }

        async function clearData() {
            if (!confirm('确定要清空所有已导入的数据吗？')) return;
            const res = await fetch('/api/clear', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'ok') { showToast('🗑️ 数据已清空'); setTimeout(() => location.reload(), 800); }
        }

        async function updateAll() {
            const date = document.getElementById('dateSel').value;
            const ip = document.getElementById('ipSel').value;
            const content = document.getElementById('analysisContent');
            const badge = document.getElementById('riskBadge');
            badge.style.display = 'none';
            content.innerHTML = '<div class="analysis-loading">🤖 AI 正在分析数据并生成报告，请稍候...</div>';

            try {
                const [chartRes, summaryRes] = await Promise.all([
                    fetch(`/api/chart?date=${encodeURIComponent(date)}&ip=${encodeURIComponent(ip)}`),
                    fetch('/api/summary')
                ]);
                chart.setOption(await chartRes.json(), true);
                const summary = await summaryRes.json();
                document.getElementById('statNodes').innerText = summary.nodes;
                document.getElementById('statRecords').innerText = summary.records;
                document.getElementById('statPeak').innerText = summary.peak + '%';
                document.getElementById('statHot').innerText = summary.hot;

                const analysisRes = await fetch(`/api/analysis?date=${encodeURIComponent(date)}&ip=${encodeURIComponent(ip)}`);
                if (!analysisRes.ok) throw new Error('分析接口返回错误');
                renderAnalysis(await analysisRes.json());
            } catch (e) {
                console.error('更新失败:', e);
                content.innerHTML = `<div class="analysis-empty">⚠️ 数据加载失败: ${e.message}</div>`;
            }
        }

        function renderAnalysis(data) {
            const badge = document.getElementById('riskBadge');
            const content = document.getElementById('analysisContent');
            if (data.empty) {
                badge.style.display = 'none';
                content.innerHTML = '<div class="analysis-empty">当前筛选条件下无数据，请导入CSV或调整IP/日期</div>';
                return;
            }
            badge.style.display = 'inline-block';
            badge.textContent = data.risk_level;
            badge.style.background = data.risk_color;

            let html = '';
            if (data.use_llm && data.llm_report) {
                html += `<div class="llm-report"><h4>🤖 AI 专家诊断报告</h4><div class="llm-text">${marked.parse(data.llm_report)}</div></div>`;
            }
            html += `
                <div class="analysis-body">
                    <div><div class="analysis-stats">
                        <div class="stat-item"><div class="label">平均使用率</div><div class="value">${data.avg}%</div></div>
                        <div class="stat-item"><div class="label">峰值 / 谷值</div><div class="value">${data.max}% / ${data.min}%</div></div>
                        <div class="stat-item"><div class="label">采样点数</div><div class="value">${data.samples}</div></div>
                        <div class="stat-item"><div class="label">风险占比(≥80%)</div><div class="value" style="color:#F56C6C">${data.high_pct}%</div></div>
                        <div class="stat-item"><div class="label">空闲占比(<20%)</div><div class="value" style="color:#67C23A">${data.idle_pct}%</div></div>
                        <div class="stat-item"><div class="label">分析范围</div><div class="value" style="font-size:13px">${data.target}<br>${data.date}</div></div>
                    </div></div>
                    <div class="analysis-suggestions">
                        <h4>💡 规则诊断建议 ${data.use_llm ? '(备用)' : ''}</h4>
                        <ul class="suggestion-list">${data.suggestions.map(s => `<li>${s}</li>`).join('')}</ul>
                    </div>
                </div>`;
            content.innerHTML = html;
        }

        window.onload = updateAll;
        window.onresize = () => chart.resize();
    </script>
</body>
</html>
"""

# ==================== 路由 ====================
@app.route("/")
def index():
    dates = [r["date"] for r in query("SELECT DISTINCT date FROM cpu_stats ORDER BY date DESC")]
    ips = [r["ip"] for r in query("SELECT DISTINCT ip FROM cpu_stats ORDER BY ip")]
    return render_template_string(HTML_TEMPLATE, dates=[ALL]+dates, ips=ips)

@app.route("/api/chart")
def api_chart():
    return jsonify(build_chart_options(request.args.get("date", ALL), request.args.get("ip", ALL)))

@app.route("/api/summary")
def api_summary():
    s = query("SELECT COUNT(DISTINCT ip) n, COUNT(*) c, IFNULL(MAX(cpu_pct),0) p FROM cpu_stats")[0]
    hot = query("SELECT COUNT(DISTINCT ip) n FROM cpu_stats WHERE cpu_pct>=?", (THRESHOLD,))[0]["n"]
    return jsonify({"nodes": s["n"], "records": s["c"], "peak": s["p"], "hot": hot})

@app.route("/api/analysis")
def api_analysis():
    return jsonify(analyze_cpu(request.args.get("date", ALL), request.args.get("ip", ALL)))

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        save_llm_config(request.json)
        return jsonify({"status": "ok"})
    return jsonify({"config": load_llm_config(), "presets": API_PRESETS})

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if 'file' not in request.files: return jsonify({"status": "error", "error": "未接收到文件"})
    f = request.files['file']
    if not f.filename.endswith('.csv'): return jsonify({"status": "error", "error": "仅支持 .csv 格式文件"})
    try:
        count, warn = parse_and_import(f.stream)
        if count == 0: return jsonify({"status": "error", "error": warn})
        return jsonify({"status": "ok", "imported": count, "warn": warn})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route("/api/clear", methods=["POST"])
def api_clear():
    with get_db() as conn: conn.execute("DELETE FROM cpu_stats")
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    init_db()
    print("🚀 CPU监控大盘已启动: http://127.0.0.1:5000")
    print("💡 提示: 首次使用请点击右上角「⚙️ AI设置」配置大模型接口")
    app.run(host="0.0.0.0", port=5000, debug=True)