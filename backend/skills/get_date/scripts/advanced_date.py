#!/usr/bin/env python3
"""
高级日期时间工具
支持时区转换、相对日期计算、多种格式输出
"""
import subprocess
import sys
from datetime import datetime, timedelta

def get_current_time(timezone=None):
    """获取当前时间，支持时区"""
    if timezone:
        result = subprocess.check_output(["TZ", timezone, "date", "+%Y-%m-%d %H:%M:%S %Z"]).decode().strip()
    else:
        result = subprocess.check_output(["date", "+%Y-%m-%d %H:%M:%S %Z"]).decode().strip()
    return result

def get_relative_date(days=0, format="%Y-%m-%d"):
    """获取相对日期（前几天/后几天）"""
    target_date = datetime.now() + timedelta(days=days)
    return target_date.strftime(format)

def format_timestamp(timestamp=None, output_format="%Y年%m月%d日 %H:%M:%S"):
    """格式化时间戳"""
    if timestamp:
        dt = datetime.fromtimestamp(int(timestamp))
    else:
        dt = datetime.now()
    return dt.strftime(output_format)

def main():
    if len(sys.argv) < 2:
        print("用法：python advanced_date.py <command> [args]")
        print("命令:")
        print("  current [timezone]  - 获取当前时间")
        print("  relative <days>     - 获取相对日期（正数=未来，负数=过去）")
        print("  format [timestamp]  - 格式化时间戳")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "current":
        timezone = sys.argv[2] if len(sys.argv) > 2 else None
        print(get_current_time(timezone))
    
    elif command == "relative":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        print(get_relative_date(days))
    
    elif command == "format":
        timestamp = sys.argv[2] if len(sys.argv) > 2 else None
        print(format_timestamp(timestamp))
    
    else:
        print(f"未知命令：{command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
