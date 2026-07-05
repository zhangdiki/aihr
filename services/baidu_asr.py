"""百度短语音识别服务 — 同步 REST API"""
import base64
import os
import subprocess
import time
from pathlib import Path

import httpx


class BaiduASR:
    """百度语音识别 — 短语音 REST API（≤60s，≤10MB）"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    ASR_URL = "https://vop.baidu.com/server_api"

    def __init__(self, api_key: str, secret_key: str, app_id: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_id = app_id
        self._token: str | None = None
        self._token_expires_at: float = 0

    async def _get_token(self) -> str:
        """获取 access_token，缓存 30 天"""
        if self._token and time.time() < self._token_expires_at - 3600:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.secret_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if "access_token" not in data:
                raise Exception(f"百度 token 获取失败: {data}")

            self._token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 2592000)
            return self._token

    async def transcribe(self, audio_path: str) -> str:
        """转写音频为文字"""
        token = await self._get_token()

        # 转换音频为 WAV 16kHz mono（百度推荐格式）
        wav_path = self._to_wav(audio_path)

        try:
            with open(wav_path, "rb") as f:
                audio_data = f.read()

            speech_b64 = base64.b64encode(audio_data).decode()

            # 已通过 _to_wav 转为 PCM WAV 16kHz mono
            rate = 16000

            body = {
                "format": "wav",
                "rate": rate,
                "channel": 1,
                "cuid": self.app_id,
                "token": token,
                "speech": speech_b64,
                "len": len(audio_data),
            }

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.ASR_URL,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("err_no") != 0:
                    raise Exception(f"百度 ASR 失败: [{data.get('err_no')}] {data.get('err_msg')}")

                result = data.get("result", [])
                if not result:
                    raise Exception("百度 ASR 未返回转写文本")

                return result[0]

        finally:
            # 清理临时 WAV
            if wav_path != audio_path:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

    @staticmethod
    def _to_wav(input_path: str) -> str:
        """转为 WAV 16kHz mono 16bit（如果不是目标格式）"""
        ext = Path(input_path).suffix.lower()
        # 已是 WAV 则跳过转换
        if ext == ".wav":
            return input_path

        output_path = input_path + ".wav"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg 转换失败: {result.stderr}")
        return output_path
