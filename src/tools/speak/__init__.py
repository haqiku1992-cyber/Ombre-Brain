from typing import Optional

import httpx

from .. import _runtime as rt

VOICE_SERVICE_URL = "https://voice-service-xxxxx.zeabur.app/generate-voice"


async def dispatch(
    text: str,
    max_tokens: Optional[int] = 0,
    max_results: Optional[int] = 0,
    **kwargs,
) -> str:
    if not text:
        return "请提供要朗读的文本。"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(VOICE_SERVICE_URL, json={"text": text})

        if response.status_code == 200:
            return f"语音已生成。音频大小：{len(response.content)} bytes。"

        return f"语音生成失败：{response.status_code} {response.text}"

    except Exception as exc:
        return f"调用语音服务失败：{type(exc).__name__}: {exc}"


rt.mark_op("speak")
