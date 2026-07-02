# src/tools/weather/__init__.py

from typing import Optional
import httpx
import urllib.parse

# 项目内部依赖（与其他工具保持一致）
from .. import runtime as rt
from .. import _common

async def dispatch(
    location: Optional[str] = "上海",
    max_tokens: Optional[int] = 0,
    max_results: Optional[int] = 0,
    **kwargs
) -> str:
    """
    weather 工具的总入口。获取指定位置的当前天气状况和空气质量。
    
    参数：
        location: 城市名称，如 "上海"
        max_tokens: 未使用，保持与其他工具接口一致
        max_results: 未使用，保持与其他工具接口一致
    """
    try:
        # 1. 地理编码
        encoded_location = urllib.parse.quote(location)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_location}&count=1&language=zh"
        
        async with httpx.AsyncClient() as client:
            geo_resp = await client.get(geo_url, timeout=15)
            geo_data = geo_resp.json()
            
        if not geo_data.get("results"):
            return f"未能找到位置：{location}"
            
        loc_info = geo_data["results"][0]
        lat = loc_info["latitude"]
        lon = loc_info["longitude"]
        name = loc_info.get("name", location)
        admin1 = loc_info.get("admin1", "")
        
        # 2. 天气和空气质量
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index&daily=sunrise,sunset&timezone=auto"
        aqi_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=pm2_5,pm10,us_aqi"
        
        async with httpx.AsyncClient() as client:
            w_resp = await client.get(weather_url, timeout=15)
            a_resp = await client.get(aqi_url, timeout=15)
            w_data = w_resp.json()
            a_data = a_resp.json()
        
        current_w = w_data.get("current", {})
        daily_w = w_data.get("daily", {})
        current_a = a_data.get("current", {})
        
        sunrise = daily_w.get("sunrise", ["未知"])[0].split("T")[-1] if daily_w.get("sunrise") else "未知"
        sunset = daily_w.get("sunset", ["未知"])[0].split("T")[-1] if daily_w.get("sunset") else "未知"
        
        # 3. 组装报告
        report = (
            f"🌟 【{name}（{admin1}）】实时天气：\n"
            f"----------------------------------------\n"
            f"🌡️ 温度：{current_w.get('temperature_2m', 'N/A')} °C\n"
            f"🧣 体感：{current_w.get('apparent_temperature', 'N/A')} °C\n"
            f"💧 湿度：{current_w.get('relative_humidity_2m', 'N/A')}%\n"
            f"💨 风速：{current_w.get('wind_speed_10m', 'N/A')} km/h\n"
            f"☀️ 紫外线：{current_w.get('uv_index', 'N/A')}\n"
            f"🌅 日出：{sunrise} | 🌇 日落：{sunset}\n"
            f"----------------------------------------\n"
            f"🍃 AQI：{current_a.get('us_aqi', 'N/A')}\n"
            f"😷 PM2.5：{current_a.get('pm2_5', 'N/A')} μg/m³\n"
            f"----------------------------------------\n"
            f"更新于：{current_w.get('time', '未知').replace('T', ' ')}"
        )
        
        return report
        
    except Exception as e:
        return f"❌ 查询失败：{type(e).__name__} - {str(e)}"
