"""飞书语音识别服务 — stream_recognize"""
import asyncio
import base64
import os
import subprocess
import time
from pathlib import Path

import httpx


class FeishuASR:
    """飞书语音识别（speech_to_text stream_recognize）"""

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str | None = None
        self._token_expires_at: float = 0

    # ---- 认证 ----

    async def _get_token(self) -> str:
        """获取 tenant_access_token，自动缓存"""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"飞书认证失败: {data.get('msg', 'Unknown')}")

            self._token = data["tenant_access_token"]
            self._token_expires_at = time.time() + data.get("expire", 7200)
            return self._token

    # ---- 音频转换 ----

    @staticmethod
    def _convert_to_pcm(input_path: str) -> str:
        """用 ffmpeg 将音频转为 PCM 16kHz mono 16bit little-endian"""
        output_path = input_path + ".pcm"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg 转换失败: {result.stderr}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise Exception("ffmpeg 转换后文件为空")

        return output_path

    # ---- 语音转写 ----

    async def transcribe(self, audio_path: str) -> str:
        """
        将音频文件转写为文字。
        短音频（≤60s）：直接 file_recognize
        长音频：stream_recognize 分片
        """
        # 获取音频时长（秒）
        duration = self._get_duration(audio_path)
        print(f"[FeishuASR] 音频时长: {duration:.1f}s")

        if duration <= 58:
            # 短音频直接用 file_recognize
            return await self._file_recognize(audio_path)
        else:
            # 长音频用 stream_recognize
            return await self._stream_recognize(audio_path)

    @staticmethod
    def _get_duration(path: str) -> float:
        """获取音频时长"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True
            )
            return float(result.stdout.strip())
        except Exception:
            return 0

    # === file_recognize（≤60s）===

    async def _file_recognize(self, audio_path: str) -> str:
        """一次性识别短音频"""
        token = await self._get_token()

        # 读取音频并 base64
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        speech_b64 = base64.b64encode(audio_data).decode()

        body = {
            "speech": speech_b64,
            "config": {
                "format": self._guess_format(audio_path),
                "engine_type": "16k_auto",
                "enable_punctuation": True,
            }
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}/speech_to_text/v1/speech/file_recognize",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=body,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"飞书 ASR 失败: [{data.get('code')}] {data.get('msg')}")

            # 解析结果
            text = data.get("data", {}).get("text", "")
            if not text:
                raise Exception("飞书 ASR 未返回转写文本")

            return text

    # === stream_recognize（长音频）===

    async def _stream_recognize(self, audio_path: str) -> str:
        """
        流式识别长音频。
        1. ffmpeg 转 PCM 16kHz
        2. 按 ~100ms 分片（每片 3200 bytes = 16000 * 2 * 0.1）
        3. 逐片发送给飞书 stream_recognize
        4. 拼接结果
        """
        # 转换音频格式
        pcm_path = self._convert_to_pcm(audio_path)

        try:
            token = await self._get_token()

            with open(pcm_path, "rb") as f:
                pcm_data = f.read()

            total_bytes = len(pcm_data)
            chunk_size = 3200  # 100ms at 16kHz 16bit mono
            chunks = []
            for i in range(0, total_bytes, chunk_size):
                chunks.append(pcm_data[i:i + chunk_size])

            print(f"[FeishuASR] stream_recognize: {total_bytes} bytes → {len(chunks)} chunks")

            all_text = []

            async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                for idx, chunk in enumerate(chunks):
                    # 每个 chunk 作为独立的 HTTP 请求发送
                    speech_b64 = base64.b64encode(chunk).decode()

                    payload = {
                        "speech": {
                            "speech": speech_b64
                        },
                        "config": {
                            "stream_config": {
                                "format": "pcm",
                                "sample_rate": 16000,
                                "enable_punctuation": True,
                                "enable_itn": True,
                            }
                        }
                    }

                    try:
                        resp = await client.post(
                            f"{self.BASE_URL}/speech_to_text/v1/speech/stream_recognize",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json; charset=utf-8",
                            },
                            json=payload,
                        )
                        resp.raise_for_status()
                        data = resp.json()

                        if data.get("code") != 0:
                            print(f"[FeishuASR] chunk {idx} 错误: [{data.get('code')}] {data.get('msg')}")
                            continue

                        text = data.get("data", {}).get("text", "")
                        if text:
                            all_text.append(text)

                    except Exception as e:
                        print(f"[FeishuASR] chunk {idx} 请求失败: {e}")
                        continue

                    # 小延迟避免触发限流
                    if idx > 0 and idx % 10 == 0:
                        await asyncio.sleep(0.05)

            full_text = "".join(all_text)
            if not full_text:
                raise Exception("飞书 stream_recognize 未返回任何转写文本")

            print(f"[FeishuASR] stream_recognize 完成: {len(full_text)} 字")
            return full_text

        finally:
            # 清理临时 PCM 文件
            try:
                os.unlink(pcm_path)
            except Exception:
                pass

    @staticmethod
    def _guess_format(path: str) -> str:
        """根据扩展名推断音频格式"""
        ext = Path(path).suffix.lower()
        mapping = {
            ".wav": "wav",
            ".mp3": "mp3",
            ".m4a": "m4a",
            ".aac": "aac",
            ".flac": "flac",
            ".ogg": "ogg",
        }
        return mapping.get(ext, "wav")
