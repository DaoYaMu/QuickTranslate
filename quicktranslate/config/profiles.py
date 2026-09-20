"""多 API 配置档案：JSON 存元数据，API Key 存系统凭据库（失败则混淆兜底）。"""

from __future__ import annotations

import base64
import hashlib
import getpass
import json
import os
import platform
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from . import paths

log = get_logger("profiles")

SERVICE_NAME = "QuickTranslate"

DEFAULT_SYSTEM_PROMPT = (
    "你是一个专业的翻译引擎。请将用户提供的文本准确、自然地翻译成目标语言，"
    "保持原文的语气、专业术语与段落换行。只输出译文本身，"
    "不要添加解释、拼音、原文或任何多余说明。"
)

DEFAULT_HEADERS_HINT = '{"Authorization": "Bearer xxx"}'

# 默认「响应超时」（秒）：等待模型开始回话的最长时间。
# 关掉思考链之后，健康的模型 1~3 秒就会吐字，90 秒足够宽松；
# 一旦模型服务端出问题（例如硅基流动某个模型排队/故障），
# 也能在 1 分半内给出明确报错，而不是长时间无响应。
DEFAULT_TIMEOUT = 90.0

# 旧版本遗留的默认值（300 秒）。只用于一次性迁移，见 ProfileStore._migrate_timeouts。
LEGACY_DEFAULT_TIMEOUT = 300.0

# 关闭思考链的推荐参数（Qwen3 / DeepSeek-R1 等，硅基流动、vLLM 都认这个键）
NO_THINKING_BODY = '{"enable_thinking": false}'

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "OpenAI",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "fallback_model": "",
    "temperature": 0.3,
    "custom_headers": "",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "extra_body": "",
    "timeout": DEFAULT_TIMEOUT,
}

# 档案里允许持久化到 JSON 的字段（api_key 单独存储，绝不写入）
_FIELDS = (
    "name",
    "base_url",
    "model",
    "fallback_model",
    "temperature",
    "custom_headers",
    "system_prompt",
    "extra_body",
    "timeout",
)


# --------------------------------------------------------------------------- #
# API Key 存储
# --------------------------------------------------------------------------- #
def _machine_key() -> bytes:
    seed = f"{platform.node()}|{getpass.getuser()}|QuickTranslate|v1"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _obfuscate(text: str) -> str:
    key = _machine_key()
    raw = text.encode("utf-8")
    masked = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return base64.b64encode(masked).decode("ascii")


def _deobfuscate(token: str) -> str:
    key = _machine_key()
    masked = base64.b64decode(token.encode("ascii"))
    raw = bytes(b ^ key[i % len(key)] for i, b in enumerate(masked))
    return raw.decode("utf-8")


class _KeyBackend:
    """优先 keyring（系统凭据库），不可用时退回混淆文件。"""

    def __init__(self, fallback_path: str) -> None:
        self._path = fallback_path
        self._keyring = None
        try:
            import keyring  # noqa: PLC0415

            keyring.get_password(SERVICE_NAME, "__probe__")
            self._keyring = keyring
            log.info("API Key 使用系统凭据库存储")
        except Exception as exc:  # keyring 无后端 / 未安装
            log.warning("keyring 不可用（%s），退回混淆存储", exc)

    @property
    def secure(self) -> bool:
        return self._keyring is not None

    def get(self, name: str) -> str:
        if self._keyring is not None:
            try:
                return self._keyring.get_password(SERVICE_NAME, name) or ""
            except Exception as exc:
                log.warning("读取凭据库失败：%s", exc)
        return self._fallback_read().get(name, "")

    def set(self, name: str, value: str) -> None:
        if self._keyring is not None:
            try:
                if value:
                    self._keyring.set_password(SERVICE_NAME, name, value)
                else:
                    try:
                        self._keyring.delete_password(SERVICE_NAME, name)
                    except Exception:
                        pass
                return
            except Exception as exc:
                log.warning("写入凭据库失败：%s", exc)
        data = self._fallback_read()
        if value:
            data[name] = _obfuscate(value)
        else:
            data.pop(name, None)
        self._fallback_write(data)

    def delete(self, name: str) -> None:
        self.set(name, "")

    def _fallback_read(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _fallback_write(self, data: dict[str, str]) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            log.error("写入兜底密钥文件失败：%s", exc)


# --------------------------------------------------------------------------- #
# 档案仓库
# --------------------------------------------------------------------------- #
class ProfileStore:
    def __init__(self, path: str | None = None) -> None:
        self._path = path or paths.profiles_path()
        self._backend = _KeyBackend(paths.fallback_keys_path())
        self._profiles: list[dict[str, Any]] = []
        self.load()

    # --- 读写 ---
    def load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                self._profiles = [self._normalize(p) for p in data if isinstance(p, dict)]
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("读取档案失败：%s", exc)

        if not self._profiles:
            self._profiles = [self._normalize(DEFAULT_PROFILE)]
            self.save()
        self._migrate_timeouts()

    def _migrate_timeouts(self) -> None:
        """一次性把旧版本遗留的 300 秒默认超时收窄到 90 秒。

        早期版本为了"思考链"给到 300 秒，而 OpenAI SDK 默认还会重试 2 次，
        于是模型服务一挂就要静默等 900 秒才报错。现在默认关闭思考链，
        90 秒足够；只动"用户没改过的默认值"，手工调过的数值保持原样。
        """
        marker = Path(self._path).parent / ".timeout_migrated_v2"
        if marker.exists():
            return
        changed = False
        for item in self._profiles:
            try:
                if float(item.get("timeout") or 0) == LEGACY_DEFAULT_TIMEOUT:
                    item["timeout"] = float(DEFAULT_TIMEOUT)
                    changed = True
            except (TypeError, ValueError):
                continue
        try:
            marker.write_text("ok", encoding="utf-8")
        except OSError:
            pass
        if changed:
            self.save()
            log.info(
                "已把超时默认值 %s 秒调整为 %s 秒",
                LEGACY_DEFAULT_TIMEOUT, DEFAULT_TIMEOUT,
            )

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._profiles, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            log.error("写入档案失败：%s", exc)

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
        item = {k: raw.get(k, DEFAULT_PROFILE[k]) for k in _FIELDS}
        item["name"] = str(item["name"] or "未命名")
        item["extra_body"] = str(item.get("extra_body") or "")
        item["fallback_model"] = str(item.get("fallback_model") or "").strip()
        try:
            item["temperature"] = float(item["temperature"])
        except (TypeError, ValueError):
            item["temperature"] = 0.3
        try:
            item["timeout"] = float(item["timeout"])
        except (TypeError, ValueError):
            item["timeout"] = float(DEFAULT_PROFILE["timeout"])
        if item["timeout"] <= 0:
            item["timeout"] = float(DEFAULT_PROFILE["timeout"])
        return item

    # --- 查询 ---
    @property
    def profiles(self) -> list[dict[str, Any]]:
        return self._profiles

    @property
    def secure_storage(self) -> bool:
        return self._backend.secure

    def names(self) -> list[str]:
        return [str(p["name"]) for p in self._profiles]

    def get(self, name: str) -> dict[str, Any] | None:
        for p in self._profiles:
            if p["name"] == name:
                return p
        return self._profiles[0] if self._profiles else None

    def first_name(self) -> str:
        return str(self._profiles[0]["name"]) if self._profiles else ""

    # --- 变更 ---
    def upsert(self, profile: dict[str, Any], original_name: str | None = None) -> None:
        """新增或更新档案。改名时同步迁移 API Key。"""
        item = self._normalize(profile)
        renamed = original_name is not None and original_name != item["name"]
        if renamed:
            key = self._backend.get(original_name)
            if key:
                self._backend.set(item["name"], key)
                self._backend.delete(original_name)

        for idx, p in enumerate(self._profiles):
            if original_name is not None and p["name"] == original_name:
                self._profiles[idx] = item
                break
            if p["name"] == item["name"]:
                self._profiles[idx] = item
                break
        else:
            self._profiles.append(item)
        self.save()

    def remove(self, name: str) -> None:
        if len(self._profiles) <= 1:
            return  # 至少保留一个档案
        self._profiles = [p for p in self._profiles if p["name"] != name]
        self._backend.delete(name)
        self.save()

    # --- Key ---
    def api_key(self, name: str) -> str:
        return self._backend.get(name)

    def set_api_key(self, name: str, key: str) -> None:
        self._backend.set(name, key.strip())
