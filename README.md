# 🖥️ CPU 监控大盘与 AI 智能诊断系统

一个基于 Flask + ECharts + LLM 的轻量级服务器 CPU 监控可视化面板。支持 CSV 数据一键导入、多维度趋势分析，并集成大语言模型（LLM）提供智能运维诊断报告。

## ✨ 核心功能

- **📊 交互式数据可视化**：基于 ECharts 实现，支持多节点对比、时间轴缩放、数据区域放大。
- **🤖 AI 智能诊断**：接入大模型（支持 OpenAI、DeepSeek、Ollama 等），自动生成专业的 SRE 运维分析报告。
- **⚙️ 可视化 AI 配置**：内置主流大模型服务商预设，界面化配置 API，支持一键切换与自定义模型。
- **📂 便捷数据导入**：支持 UTF-8/GBK 编码自动识别的 CSV 文件上传与解析。
- **🛡️ 优雅降级机制**：当 AI 接口超时或不可用时，自动切换至内置的规则引擎进行基础诊断，保证服务高可用。
- **📱 响应式设计**：完美适配 PC 与移动端浏览器。

## 📸 界面预览

*(建议在此处放上你的项目截图，例如：)*
<!-- ![Dashboard](assets/screenshot.png) -->

## 🚀 快速开始

### 环境要求
- Python 3.8+

### 安装与运行

1. **克隆项目**
   ```bash
   git clone https://github.com/your-username/cpu-monitor-dashboard.git
   cd cpu-monitor-dashboard