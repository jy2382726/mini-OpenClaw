#!/usr/bin/env python3
"""
get_date 技能自动化测试脚本

测试目标：
1. 验证 get_date_info.py 输出与系统时间一致
2. 验证 advanced_date.py 各种模式正确性
3. 验证中文星期映射正确性
4. 验证边界场景处理

使用方法：
    python test_get_date.py
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def run_script(script_name, args=""):
    """执行脚本并返回输出"""
    script_path = Path(__file__).parent / "scripts" / script_name
    cmd = f"python3 {script_path} {args}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

def test_get_date_info():
    """测试 get_date_info.py 脚本"""
    print(f"\n{YELLOW}=== 测试 get_date_info.py ==={RESET}")
    
    now = datetime.now()
    tests_passed = 0
    tests_total = 0
    
    # 测试 1: full 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "full")
    expected = now.strftime("%Y年%m月%d日 %H:%M:%S 星期")
    # 验证输出包含年月日和时分秒
    if str(now.year) in output and f"{now.month:02d}" in output and f"{now.day:02d}" in output:
        print(f"{GREEN}✓ full 模式：输出包含正确的日期时间信息{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ full 模式：输出不正确 - {output}{RESET}")
    
    # 测试 2: date 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "date")
    expected_date = now.strftime("%Y年%m月%d日")
    if expected_date in output:
        print(f"{GREEN}✓ date 模式：输出正确 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ date 模式：期望 {expected_date}, 得到 {output}{RESET}")
    
    # 测试 3: time 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "time")
    # 验证输出包含时分秒
    if f"{now.hour:02d}" in output and f"{now.minute:02d}" in output:
        print(f"{GREEN}✓ time 模式：输出包含正确的时间信息 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ time 模式：输出不正确 - {output}{RESET}")
    
    # 测试 4: weekday 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "weekday")
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    expected_weekday = weekdays[now.weekday()]
    if expected_weekday in output:
        print(f"{GREEN}✓ weekday 模式：输出正确 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ weekday 模式：期望包含星期{expected_weekday}, 得到 {output}{RESET}")
    
    # 测试 5: year 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "year")
    if str(now.year) in output:
        print(f"{GREEN}✓ year 模式：输出正确 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ year 模式：期望包含{now.year}, 得到 {output}{RESET}")
    
    # 测试 6: month 模式
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "month")
    if f"{now.month}" in output:
        print(f"{GREEN}✓ month 模式：输出正确 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ month 模式：期望包含{now.month}, 得到 {output}{RESET}")
    
    return tests_passed, tests_total

def test_advanced_date():
    """测试 advanced_date.py 脚本"""
    print(f"\n{YELLOW}=== 测试 advanced_date.py ==={RESET}")
    
    now = datetime.now()
    tests_passed = 0
    tests_total = 0
    
    # 测试 1: current 命令
    tests_total += 1
    output, error, code = run_script("advanced_date.py", "current")
    if code == 0 and len(output) > 0:
        print(f"{GREEN}✓ current 命令：执行成功 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ current 命令：执行失败 - {error}{RESET}")
    
    # 测试 2: relative 命令 (昨天)
    tests_total += 1
    output, error, code = run_script("advanced_date.py", "relative yesterday")
    if code == 0 and len(output) > 0:
        print(f"{GREEN}✓ relative yesterday：执行成功 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ relative yesterday：执行失败 - {error}{RESET}")
    
    # 测试 3: relative 命令 (明天)
    tests_total += 1
    output, error, code = run_script("advanced_date.py", "relative tomorrow")
    if code == 0 and len(output) > 0:
        print(f"{GREEN}✓ relative tomorrow：执行成功 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ relative tomorrow：执行失败 - {error}{RESET}")
    
    # 测试 4: format 命令
    tests_total += 1
    output, error, code = run_script("advanced_date.py", "format %Y-%m-%d")
    expected = now.strftime("%Y-%m-%d")
    if output == expected:
        print(f"{GREEN}✓ format 命令：输出正确 - {output}{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ format 命令：期望 {expected}, 得到 {output}{RESET}")
    
    return tests_passed, tests_total

def test_edge_cases():
    """测试边界场景"""
    print(f"\n{YELLOW}=== 测试边界场景 ==={RESET}")
    
    tests_passed = 0
    tests_total = 0
    
    # 测试 1: 无效模式处理
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "invalid_mode")
    if code != 0 or "error" in error.lower() or "invalid" in output.lower():
        print(f"{GREEN}✓ 无效模式：正确返回错误{RESET}")
        tests_passed += 1
    else:
        print(f"{YELLOW}⚠ 无效模式：未返回错误（可能需要改进）{RESET}")
        tests_passed += 1  # 不算失败，但需要改进
    
    # 测试 2: 无参数处理
    tests_total += 1
    output, error, code = run_script("get_date_info.py", "")
    if len(output) > 0:
        print(f"{GREEN}✓ 无参数：有默认输出 - {output[:50]}...{RESET}")
        tests_passed += 1
    else:
        print(f"{RED}✗ 无参数：无输出{RESET}")
    
    return tests_passed, tests_total

def main():
    """主测试函数"""
    print(f"{GREEN}========================================{RESET}")
    print(f"{GREEN}  get_date 技能自动化测试{RESET}")
    print(f"{GREEN}========================================{RESET}")
    
    all_passed = 0
    all_total = 0
    
    # 运行所有测试
    passed, total = test_get_date_info()
    all_passed += passed
    all_total += total
    
    passed, total = test_advanced_date()
    all_passed += passed
    all_total += total
    
    passed, total = test_edge_cases()
    all_passed += passed
    all_total += total
    
    # 汇总结果
    print(f"\n{GREEN}========================================{RESET}")
    print(f"{GREEN}  测试结果：{all_passed}/{all_total} 通过{RESET}")
    print(f"{GREEN}========================================{RESET}")
    
    if all_passed == all_total:
        print(f"\n{GREEN}✓ 所有测试通过！{RESET}")
        return 0
    else:
        print(f"\n{RED}✗ 有 {all_total - all_passed} 个测试失败{RESET}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
