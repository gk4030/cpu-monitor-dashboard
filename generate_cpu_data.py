# generate_cpu_data.py
import csv
import random
from datetime import datetime, timedelta

# 1. 定义基础配置（基于你提供的图片）
IPS = [
    "100.94.112.79", "100.94.112.77", "100.94.112.78", 
    "100.94.112.75", "100.94.112.76", "100.94.112.73", 
    "100.94.112.74", "100.94.112.71"
]

# 每天的时间区间 (假设每5分钟一个采样点，9小时共108个采样点，符合图片中的 Samples=108)
# 图片显示 Time Range 是 00:00-09:00，我们构造几个典型区间来模拟一天的数据
# 为了数据量适中且能看出趋势，我们构造一天 24 个小时，每小时一个区间 (或者按图片的 9小时区间逻辑)
# 这里为了演示效果，我们生成一天 24 个区间 (00:00-01:00 ... 23:00-00:00)，每个区间 Samples=60 (每分钟1次)
# 或者严格模仿图片：只生成 00:00-09:00 这种大区间？
# 不，图片里的 Time Range 是 "00:00-09:00"，这通常意味着这是一个汇总报表。
# 为了让趋势图有“趋势”，我们需要一天内有多个时间点。
# 让我们假设一天分为 24 个区间，每个区间 1 小时。
TIME_RANGES = [f"{h:02d}:00-{(h+1)%24:02d}:00" for h in range(24)]
SAMPLES_PER_RANGE = 60 # 假设每分钟采样一次

START_DATE = datetime(2026, 8, 24)
END_DATE = datetime(2026, 8, 31)
OUTPUT_FILE = "cpu.csv"

def generate_row(ip, date_str, time_range, base_load):
    """生成单行数据，模拟 CPU 波动"""
    # 基础负载 + 随机波动 (-10 到 +15)
    # 特殊处理 100.94.112.73，让它更容易飙高
    if ip == "100.94.112.73":
        base_load += 20 
    
    current_load = max(5, min(98, base_load + random.uniform(-10, 15)))
    
    # 模拟区间内的 Max, Min, Avg
    max_pct = min(100, current_load + random.uniform(5, 20))
    min_pct = max(0, current_load - random.uniform(5, 15))
    avg_pct = current_load
    
    # 模拟具体发生时间 (在区间内随机选一个时间)
    start_h, start_m = map(int, time_range.split("-")[0].split(":"))
    # 简单处理跨天情况 (23:00-00:00)
    end_h = (start_h + 1) % 24
    
    max_time_obj = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=start_h, minute=random.randint(0, 59))
    min_time_obj = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=start_h, minute=random.randint(0, 59))
    
    return {
        "Node IP": ip,
        "Date": date_str,
        "Time Range": time_range,
        "Max(%)": f"{max_pct:.0f}",
        "Max Time": max_time_obj.strftime("%Y-%m-%d %H:%M:%S"),
        "Min(%)": f"{min_pct:.0f}",
        "Min Time": min_time_obj.strftime("%Y-%m-%d %H:%M:%S"),
        "Avg(%)": f"{avg_pct:.2f}",
        "Samples": str(SAMPLES_PER_RANGE)
    }

def main():
    rows = []
    current_date = START_DATE
    
    print(f"正在生成 {START_DATE.date()} 到 {END_DATE.date()} 的模拟数据...")
    
    while current_date <= END_DATE:
        date_str = current_date.strftime("%Y-%m-%d")
        
        for ip in IPS:
            # 给每个 IP 一个随机的基础负载趋势 (正弦波模拟昼夜规律)
            for i, tr in enumerate(TIME_RANGES):
                # 模拟白天负载高，晚上负载低
                hour_factor = abs(12 - i) / 12.0 # 0(中午) -> 1(午夜)
                base_load = 30 + (1 - hour_factor) * 40 + random.uniform(-5, 5)
                
                row = generate_row(ip, date_str, tr, base_load)
                rows.append(row)
        
        current_date += timedelta(days=1)

    # 写入 CSV
    # 注意：表头必须与图片完全一致，包括空格
    fieldnames = ["Node IP", "Date", "Time Range", "Max(%)", "Max Time", "Min(%)", "Min Time", "Avg(%)", "Samples"]
    
    with open(OUTPUT_FILE, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        
    print(f"成功生成 {len(rows)} 条记录，已保存至 {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
