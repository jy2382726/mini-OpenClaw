#!/usr/bin/env python3
"""
获取当前日期时间，支持中文星期输出
"""
import subprocess
import sys
from datetime import datetime

def get_date_info(format_type="full"):
    """获取日期时间信息"""
    
    # 中文星期映射
    weekday_map = {
        "Monday": "星期一",
        "Tuesday": "星期二",
        "Wednesday": "星期三",
        "Thursday": "星期四",
        "Friday": "星期五",
        "Saturday": "星期六",
        "Sunday": "星期日"
    }
    
    if format_type == "full":
        # 获取完整信息
        date_str = subprocess.check_output(["date", "+%Y年%m月%d日 %H:%M:%S"]).decode().strip()
        weekday_en = subprocess.check_output(["date", "+%A"]).decode().strip()
        weekday_cn = weekday_map.get(weekday_en, weekday_en)
        return f"{date_str}\n今天是：{weekday_cn}"
    
    elif format_type == "date":
        return subprocess.check_output(["date", "+%Y年%m月%d日"]).decode().strip()
    
    elif format_type == "time":
        return subprocess.check_output(["date", "+%H:%M:%S"]).decode().strip()
    
    elif format_type == "weekday":
        weekday_en = subprocess.check_output(["date", "+%A"]).decode().strip()
        return weekday_map.get(weekday_en, weekday_en)
    
    elif format_type == "year":
        return subprocess.check_output(["date", "+%Y年"]).decode().strip()
    
    elif format_type == "month":
        return subprocess.check_output(["date", "+%m月"]).decode().strip()
    
    else:
        # 默认返回完整信息
        return get_date_info("full")

if __name__ == "__main__":
    format_type = sys.argv[1] if len(sys.argv) > 1 else "full"
    print(get_date_info(format_type))
