import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
PLACEHOLDER_API_KEYS = {"", "your_api_key_here"}


@dataclass(frozen=True)
class ChatConfig:
    provider: str  # "anthropic" | "openai"
    api_key: str
    base_url: str
    model: str

    @property
    def is_configured(self) -> bool:
        return self.api_key.strip() not in PLACEHOLDER_API_KEYS


def _env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _read_setting(name: str, file_values: dict[str, str], default: str = "") -> str:
    # .env 是本 app 的主配置入口：文件里有非空值就优先用它，
    # 避免外部 shell 注入的空/错误环境变量（如空 ANTHROPIC_API_KEY）把 .env 覆盖掉。
    # .env 未配置该项时再回退到环境变量，最后用 default。
    file_val = file_values.get(name, "").strip()
    if file_val:
        return file_val
    return os.getenv(name, default).strip()


def get_chat_config(env_file: Path | None = None) -> ChatConfig:
    file_values = _load_dotenv(env_file or _env_path())
    provider = (_read_setting("CHAT_PROVIDER", file_values, "anthropic") or "anthropic").lower()
    if provider not in {"anthropic", "openai"}:
        provider = "anthropic"

    if provider == "openai":
        return ChatConfig(
            provider="openai",
            api_key=_read_setting("OPENAI_API_KEY", file_values),
            base_url=_read_setting("OPENAI_BASE_URL", file_values),
            model=_read_setting("OPENAI_MODEL", file_values, DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL,
        )

    return ChatConfig(
        provider="anthropic",
        api_key=_read_setting("ANTHROPIC_API_KEY", file_values),
        base_url=_read_setting("ANTHROPIC_BASE_URL", file_values),
        model=_read_setting("ANTHROPIC_MODEL", file_values, DEFAULT_MODEL) or DEFAULT_MODEL,
    )
