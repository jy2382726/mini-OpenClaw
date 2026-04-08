#!/usr/bin/env python3
"""
测试 OpenWeather API 技能
"""

import os
import sys
import requests
import json

def test_openweather_api(city="Beijing"):
    """测试 OpenWeather API 调用"""
    
    # 从环境变量获取 API 密钥
    api_key = os.getenv("OPENWEATHER_API_KEY")
    
    if not api_key:
        print("❌ 错误：未设置 OPENWEATHER_API_KEY 环境变量")
        print("请在 .env 文件中添加：OPENWEATHER_API_KEY=your_api_key_here")
        return False
    
    print(f"🔍 正在测试 OpenWeather API，查询城市：{city}")
    
    # 构建 API 请求 URL
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric&lang=zh_cn"
    
    try:
        # 发送请求
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # 提取天气信息
            weather_info = {
                "城市": data.get("name", "未知"),
                "国家": data.get("sys", {}).get("country", "未知"),
                "温度": f"{data.get('main', {}).get('temp', '未知')}°C",
                "体感温度": f"{data.get('main', {}).get('feels_like', '未知')}°C",
                "天气状况": data.get('weather', [{}])[0].get('description', '未知'),
                "湿度": f"{data.get('main', {}).get('humidity', '未知')}%",
                "气压": f"{data.get('main', {}).get('pressure', '未知')} hPa",
                "风速": f"{data.get('wind', {}).get('speed', '未知')} m/s",
                "风向": data.get('wind', {}).get('deg', '未知'),
                "能见度": f"{data.get('visibility', 0) / 1000} km" if data.get('visibility') else "未知"
            }
            
            print("✅ API 调用成功！")
            print("📊 天气信息：")
            for key, value in weather_info.items():
                print(f"  {key}: {value}")
            
            return True
            
        else:
            print(f"❌ API 调用失败，状态码：{response.status_code}")
            print(f"错误信息：{response.text}")
            
            # 如果是 401 错误，可能是 API 密钥无效
            if response.status_code == 401:
                print("💡 提示：请检查 OPENWEATHER_API_KEY 是否正确")
            
            return False
            
    except requests.exceptions.Timeout:
        print("❌ 请求超时，请检查网络连接")
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ 网络请求错误：{e}")
        return False
    except Exception as e:
        print(f"❌ 未知错误：{e}")
        return False

if __name__ == "__main__":
    # 获取命令行参数中的城市名
    city = sys.argv[1] if len(sys.argv) > 1 else "Beijing"
    
    print("=" * 50)
    print("OpenWeather API 测试脚本")
    print("=" * 50)
    
    success = test_openweather_api(city)
    
    print("=" * 50)
    if success:
        print("✅ 测试通过！新技能可以正常工作")
    else:
        print("❌ 测试失败，请检查配置")
    
    print("
💡 使用说明：")
    print("1. 在 https://openweathermap.org/api 注册并获取 API 密钥")
    print("2. 在 .env 文件中设置 OPENWEATHER_API_KEY=your_api_key")
    print("3. 运行：python test_openweather.py [城市名]")
    print("   例如：python test_openweather.py Shanghai")
