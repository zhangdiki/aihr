"""百度语音识别服务 — 短/长语音 REST API

短音频（≤55s）：直接调短语音 API，同步返回
长音频（>55s）：ffmpeg 智能切片 → 逐片识别 → 拼接结果
"""
import asyncio
import base64
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
import time
from pathlib import Path
from typing import Callable, Awaitable

import httpx


class BaiduASR:
    """百度语音识别 — 短语音 REST API + 长音频自动切片"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    ASR_URL = "https://vop.baidu.com/server_api"
    CHUNK_DURATION = 55       # 每片最长秒数（留 5s 余量）
    MAX_RETRIES = 3            # 单次识别最多重试次数
    RETRY_BASE_DELAY = 1.0     # 重试基础延迟（秒）

    # 百度 dev_pid 模型速查
    DEV_PID_MAP = {
        1537: "普通话(通用, 无标点)",
        1737: "英语",
        1637: "粤语",
        1837: "四川话",
        1936: "普通话(远场)",
        80001: "短语音极速版(高精度+标点)",
    }

    def __init__(self, api_key: str, secret_key: str, app_id: str, dev_pid: int = 1537):
        self.api_key = api_key
        self.secret_key = secret_key
        self.app_id = app_id
        self.dev_pid = int(os.getenv("BAIDU_DEV_PID", str(dev_pid)))
        self._token: str | None = None
        self._token_expires_at: float = 0
        print(f"[BaiduASR] dev_pid={self.dev_pid} ({self.DEV_PID_MAP.get(self.dev_pid, '未知')})")

    # ── 认证 ──

    async def _get_token(self) -> str:
        """获取 access_token，缓存至过期前 1 小时"""
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

    # ── 公共入口 ──

    async def transcribe(
        self,
        audio_path: str,
        on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> str:
        """
        转写音频为文字。

        - 短音频（≤55s）：直接调用短语音 API
        - 长音频（>55s）：ffmpeg 切片后逐片识别，拼接返回

        on_progress(current, total, status) — 可选进度回调
        """
        duration = self._get_duration(audio_path)
        print(f"[BaiduASR] 原始音频时长: {duration:.1f}s")

        # WebM 等格式可能缺少时长元数据，先转为 WAV 再测真实时长
        if duration <= 0:
            wav_path = self._to_wav(audio_path)
            duration = self._get_duration(wav_path)
            print(f"[BaiduASR] 转 WAV 后时长: {duration:.1f}s")
            if wav_path != audio_path:
                self._rm(wav_path)  # 清理临时 WAV，后续路径会重新生成

        if duration <= self.CHUNK_DURATION:
            return await self._recognize_short(audio_path)

        return await self._recognize_long(audio_path, duration, on_progress)

    # ── 短语音识别（≤55s）──

    async def _recognize_short(self, audio_path: str) -> str:
        """单次短语音识别（带重试）"""
        wav_path = self._to_wav(audio_path)
        try:
            text = await self._call_asr_api_with_retry(wav_path)
            if not text:
                raise Exception("百度 ASR 返回空文本")
            return text
        finally:
            if wav_path != audio_path:
                self._rm(wav_path)

    # ── 长语音识别（>55s）──

    async def _recognize_long(
        self,
        audio_path: str,
        duration: float,
        on_progress: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> str:
        """切片 → 逐片识别 → 拼接"""
        # 先转为 WAV（后续切片基于 WAV）
        wav_path = self._to_wav(audio_path)
        is_temp_wav = (wav_path != audio_path)

        # ffmpeg 切片
        chunk_dir = tempfile.mkdtemp(prefix="aihr_chunks_")
        chunks = self._split_wav(wav_path, chunk_dir)
        total = len(chunks)
        print(f"[BaiduASR] 长音频切片: {total} 片")

        results: list[str] = []
        for i, chunk_path in enumerate(chunks):
            status = f"识别中 {i + 1}/{total}"
            print(f"[BaiduASR] {status}")
            if on_progress:
                await on_progress(i + 1, total, status)

            text = await self._call_asr_api_with_retry(chunk_path)
            if text:
                results.append(text)

        # 清理
        for p in chunks:
            self._rm(p)
        self._rm(chunk_dir)
        if is_temp_wav:
            self._rm(wav_path)

        full_text = "".join(results)
        if not full_text:
            raise Exception("百度 ASR 长音频识别：所有切片均未返回文本")

        print(f"[BaiduASR] 长音频识别完成: {total} 片 → {len(full_text)} 字")
        return full_text

    # ── API 调用核心 ──

    async def _call_asr_api(self, wav_path: str) -> str:
        """单次调用百度短语音 REST API。先尝试 wav 格式，失败回退到 pcm"""
        token = await self._get_token()

        with open(wav_path, "rb") as f:
            audio_data = f.read()

        # 检查音频振幅 + 保存调试文件
        raw_data = audio_data[44:] if len(audio_data) > 44 and audio_data[:4] == b'RIFF' else audio_data
        samples = len(raw_data) // 2
        max_val = 0
        for i in range(0, min(samples, 1000)):
            val = abs(struct.unpack_from('<h', raw_data, i * 2)[0])
            if val > max_val: max_val = val
        print(f"[BaiduASR] 音频有效: {samples}采样, 振幅{max_val}/{32767} ({int(max_val/32767*100)}%)")

        # 保存一份调试用 WAV 到 static 目录
        debug_path = Path(__file__).parent.parent / "static" / "_debug_asr.wav"
        shutil.copy2(wav_path, debug_path)
        print(f"[BaiduASR] 调试文件已保存: {debug_path}")

        speech_b64 = base64.b64encode(audio_data).decode()

        body = {
            "format": "wav",
            "rate": 16000,
            "channel": 1,
            "cuid": self.app_id,
            "token": token,
            "speech": speech_b64,
            "len": len(audio_data),
            "dev_pid": self.dev_pid,
        }
        print(f"[BaiduASR] WAV {len(audio_data)} bytes, rate={body['rate']}, ch={body['channel']}")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.ASR_URL,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()

        err_no = data.get("err_no", -1)
        if err_no != 0:
            err_msg = data.get("err_msg", "未知错误")
            raise ASRError(err_no, err_msg)

        result = data.get("result", [])
        return result[0] if result else ""

    async def _call_asr_api_with_retry(self, wav_path: str) -> str:
        """带重试的 ASR API 调用"""
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._call_asr_api(wav_path)
            except ASRError as e:
                last_error = e
                if e.err_no in (3301, 3308):  # 服务端过载 / 音频质量差 — 重试
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"[BaiduASR] 重试 {attempt + 1}/{self.MAX_RETRIES}, "
                          f"{delay:.1f}s 后重试 (err_no={e.err_no})")
                    await asyncio.sleep(delay)
                else:
                    raise  # 不可重试的错误直接抛
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                print(f"[BaiduASR] 网络错误重试 {attempt + 1}/{self.MAX_RETRIES}, "
                      f"{delay:.1f}s 后重试")
                await asyncio.sleep(delay)

        raise last_error  # type: ignore

    # ── 音频工具 ──

    @staticmethod
    def _to_wav(input_path: str) -> str:
        """转为 WAV 16kHz mono 16bit（非目标格式时用 ffmpeg 转换）"""
        # 检查实际文件头，不信任扩展名（前端可能发 WebM 但加 .wav 后缀）
        is_actual_wav = False
        try:
            with open(input_path, "rb") as f:
                header = f.read(12)
            is_actual_wav = header[:4] == b"RIFF" and header[8:] == b"WAVE"
        except OSError:
            pass
        ext = Path(input_path).suffix.lower()
        if ext == ".wav" and is_actual_wav:
            return input_path

        output_path = input_path + ".wav"
        cmd = [
            "ffmpeg", "-y",
            "-fflags", "+genpts",
            "-i", input_path,
            "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # 方案2: 如果方案1失败或输出无声音，尝试先复制流修复容器再转换
        if result.returncode != 0:
            print(f"[BaiduASR] 方案1失败: {result.stderr}, 尝试方案2...")
            fixed_path = input_path + ".fixed.webm"
            fix_cmd = [
                "ffmpeg", "-y",
                "-fflags", "+genpts",
                "-i", input_path,
                "-c", "copy",
                fixed_path,
            ]
            subprocess.run(fix_cmd, capture_output=True, text=True)
            cmd = [
                "ffmpeg", "-y",
                "-i", fixed_path,
                "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self._rm(fixed_path)

        if result.returncode != 0:
            raise Exception(f"ffmpeg 转换失败: {result.stderr}")
        return output_path

    @staticmethod
    def _split_wav(wav_path: str, output_dir: str) -> list[str]:
        """用 ffmpeg 将 WAV 切为 ≈CHUNK_DURATION 秒的片段"""
        pattern = os.path.join(output_dir, "chunk_%03d.wav")
        cmd = [
            "ffmpeg", "-y", "-i", wav_path,
            "-f", "segment",
            "-segment_time", str(BaiduASR.CHUNK_DURATION),
            "-c", "copy",
            pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg 切片失败: {result.stderr}")

        chunks = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.endswith(".wav")
        ])
        if not chunks:
            raise Exception("ffmpeg 切片后无文件输出")
        return chunks

    @staticmethod
    def _get_duration(path: str) -> float:
        """ffprobe 获取音频时长（秒）"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True,
            )
            return float(result.stdout.strip())
        except (ValueError, FileNotFoundError):
            return 0

    @staticmethod
    def _rm(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            try:
                os.rmdir(path)
            except OSError:
                pass


class ASRError(Exception):
    """百度 ASR 业务错误"""

    def __init__(self, err_no: int, err_msg: str):
        self.err_no = err_no
        self.err_msg = err_msg
        super().__init__(f"[{err_no}] {err_msg}")

    # 常见错误码速查表
    ERRORS = {
        3300: "输入参数不正确",
        3301: "音频质量过差 / 识别失败",
        3302: "鉴权失败",
        3303: "服务端问题",
        3304: "请求并发超限",
        3305: "服务器繁忙",
        3307: "音频文件过大（>10MB）",
        3308: "音频过长（>60s）",
        3309: "音频格式不支持",
    }
