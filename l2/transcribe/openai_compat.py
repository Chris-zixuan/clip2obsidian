"""云端转写插槽：OpenAI 兼容的 `/audio/transcriptions` 端点。

为什么是「OpenAI 兼容」而不是某个具体厂商
--------------------------------------
`provider = auto` 默认走本地，云端只是留给「本地效果不好时换服务商」的插槽。
OpenAI 兼容是当前事实标准，OpenAI、硅基流动等都支持，换厂商只改 base_url 与 model。

不依赖对象存储
--------------
直接 multipart 上传本地音频，不需要把文件放到公网可访问的 URL ——
这正是放弃通义听悟（离线转写必须给公网 FileLink，要额外接 OSS）的原因。

**本次未实测**（当前没有凭据）：配置齐了才会被选中，`doctor` 只检查配置完整性、
不发起真实调用。

小米 MiMo 不在本文件的覆盖范围内：它走 `chat/completions` 且音频需 Base64 且 ≤10MB，
若要用需另加一个 provider。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from core import config as config_mod
from core import paths
from core.textnorm import to_simplified
from l2.media import probe_duration
from l2.transcribe import chunking
from l2.transcribe.base import ProgressCb, TranscribeError, Transcript, WARN_ASR

USER_AGENT = "clip2obsidian/1.0"


class CloudProvider:
    """OpenAI 兼容端点的转写实现。"""

    name = "cloud"

    def available(self, cfg: config_mod.Config) -> tuple[bool, str]:
        if not cfg.transcribe.cloud.configured:
            return False, "未配置 [transcribe.cloud] 的 base_url 与 api_key"
        return True, ""

    def transcribe(
        self,
        audio: Path,
        *,
        cfg: config_mod.Config,
        on_progress: ProgressCb | None = None,
    ) -> Transcript:
        ok, why = self.available(cfg)
        if not ok:
            raise TranscribeError(f"云端转写不可用：{why}")

        audio = Path(audio)
        total = probe_duration(audio, cfg)
        chunk_sec = cfg.transcribe.chunk_sec

        chunk_dir = paths.work_dir(audio.stem) / "chunks"
        chunks = chunking.split(
            audio, chunk_dir, cfg=cfg, total=total, chunk_sec=chunk_sec
        )

        segments: list[dict] = []
        language = ""
        count = len(chunks)
        for chunk in chunks:
            piece, piece_lang = self._call(chunk.path, cfg)
            language = language or piece_lang
            is_last = chunk.index == count - 1
            for seg in piece:
                start = chunk.offset + float(seg["start"])
                if not is_last and start >= chunk.offset + chunk_sec:
                    continue
                segments.append({
                    "start": round(start, 2),
                    "end": round(chunk.offset + float(seg["end"]), 2),
                    "text": seg["text"],
                })
            if on_progress:
                done = min(chunk.offset + chunk.length, total) if total else chunk.offset + chunk.length
                on_progress(done, total, f"第 {chunk.index + 1}/{count} 片")

        if not segments:
            raise TranscribeError("云端转写结果为空，请检查音频内容与模型配置。")

        return Transcript(
            segments=segments,
            extractor=f"cloud:{cfg.transcribe.cloud.model}",
            language=language or cfg.transcribe.language,
            duration=total,
            warnings=[WARN_ASR],
        )

    # -------------------------------------------------------------- HTTP
    def _call(self, wav: Path, cfg: config_mod.Config) -> tuple[list[dict], str]:
        cloud = cfg.transcribe.cloud
        url = cloud.base_url.rstrip("/") + "/audio/transcriptions"
        fields = {
            "model": cloud.model,
            "response_format": "verbose_json",   # 要分段时间轴，而不是一整块文本
            "language": cfg.transcribe.language,
        }
        if cfg.transcribe.initial_prompt:
            fields["prompt"] = cfg.transcribe.initial_prompt

        body, content_type = _encode_multipart(fields, "file", wav)
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Authorization": f"Bearer {cloud.api_key}",
                "User-Agent": USER_AGENT,
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=cloud.timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")[:500]
            except OSError:
                pass
            raise TranscribeError(
                f"云端转写请求失败：HTTP {e.code}\n"
                f"  {_mask_secrets(detail, cloud.api_key)}\n"
                f"  端点：{url}"
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TranscribeError(
                f"云端转写请求失败：{e}\n  端点：{url}\n  检查网络与 base_url。"
            ) from e

        return _parse_payload(payload), str(payload.get("language") or "")


def _parse_payload(payload: dict) -> list[dict]:
    """把 verbose_json 结果转成统一的 segments。

    部分实现只回一整块 `text`（不支持 verbose_json），那就退化成单段 ——
    宁可没有细粒度时间轴，也不要报错丢掉整篇转写。
    """
    raw = payload.get("segments") or []
    segments: list[dict] = []
    for item in raw:
        text = to_simplified(str(item.get("text") or "").strip())
        if not text:
            continue
        segments.append({
            "start": float(item.get("start") or 0.0),
            "end": float(item.get("end") or 0.0),
            "text": text,
        })
    if segments:
        return segments

    text = to_simplified(str(payload.get("text") or "").strip())
    if not text:
        return []
    return [{"start": 0.0, "end": float(payload.get("duration") or 0.0), "text": text}]


def _encode_multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    """手写 multipart —— 只为省掉 requests 依赖。"""
    boundary = f"----c2o{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _mask_secrets(text: str, *secrets: str) -> str:
    """错误信息里不能出现明文密钥（服务端经常把请求头回显在报错里）。"""
    for secret in secrets:
        if secret and len(secret) > 8:
            text = text.replace(secret, secret[:4] + "****")
    return text


PROVIDER = CloudProvider()
