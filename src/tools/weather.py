# tools/weather.py
from mcp.server.fastmcp import tool as mcp_tool

@mcp_tool()
async def ombre_weather(location: str = "上海") -> str:
    """
    获取指定位置的当前天气状况和空气质量。
    """
    import httpx
    import urllib.parse
    try:
        encoded_location = urllib.parse.quote(location)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_location}&count=1&language=zh"
        
        async with httpx.AsyncClient(verify=False) as client:
            geo_resp = await client.get(geo_url, timeout=15)
            geo_data = geo_resp.json()
        
        if not geo_data.get("results"):
            return f"未能找到位置：{location}。"
        
        loc_info = geo_data["results"][0]
        lat = loc_info["latitude"]
        lon = loc_info["longitude"]
        name = loc_info.get("name", location)
        admin1 = loc_info.get("admin1", "")
        
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index&daily=sunrise,sunset&timezone=auto"
        aqi_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=pm2_5,pm10,us_aqi"
        
        async with httpx.AsyncClient(verify=False) as client:
            w_resp = await client.get(weather_url, timeout=15)
            a_resp = await client.get(aqi_url, timeout=15)
            w_data = w_resp.json()
            a_data = a_resp.json()
        
        current_w = w_data.get("current", {})
        daily_w = w_data.get("daily", {})
        current_a = a_data.get("current", {})
        
        sunrise = daily_w.get("sunrise", ["未知"])[0].split("T")[-1] if daily_w.get("sunrise") else "未知"
        sunset = daily_w.get("sunset", ["未知"])[0].split("T")[-1] if daily_w.get("sunset") else "未知"
        
        report = f"【{name}（{admin1}）】当前天气：\n实时温度 {current_w.get('temperature_2m')}°C | 体感 {current_w.get('apparent_temperature')}°C\n湿度 {current_w.get('relative_humidity_2m')}% | 降水 {current_w.get('precipitation')}mm\nAQI {current_a.get('us_aqi')} | PM2.5 {current_a.get('pm2_5')}"
        return report 突然想查一下天气，让我来看看
    except Exception as e:
        return f"天气工具异常: {e}"
