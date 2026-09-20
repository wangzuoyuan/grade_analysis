import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
PLACEHOLDER_API_KEYS = {"", "your_api_key_here"}

# 配置值常从网页/文档复制粘贴：连字符变体（不间断连字符等）归一成普通减号，
# 零宽字符直接删除——否则模型名对不上（智谱 1211 模型不存在）、base_url 打不中路由
_CHAR_NORMALIZE = str.maketrans(
    {char: "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2212\ufe63\uff0d"}
    | {char: None for char in "\u200b\u200c\u200d\ufeff"}
)


@dataclass(frozen=True)
class ChatConfig:
    provider: str  # "anthropic" | "openai"
    api_key: str
    base_url: str
    model: str
    # Anthropic 接口 max_tokens 必填所以给大数默认；OpenAI 分支不使用该值
    # （不传 max_tokens，SDK 走模型默认）
    max_tokens: int = 16384

    @property
    def is_configured(self) -> bool:
        return self.api_key.strip() not in PLACEHOLDER_API_KEYS


def _env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _clean_value(raw: str) -> str:
    """剥掉行内注释与包裹引号。.env.example 的「值 # 注释」示例风格里
    注释不是值的一部分——曾致 base_url 整串污染（网关 404 NOT_FOUND）；
    引号包裹的值内部 # 属合法内容，不剥。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    value = value.split(" #", 1)[0].strip()
    return value.strip('"').strip("'")


def _normalize_pasted(value: str) -> str:
    return value.translate(_CHAR_NORMALIZE).strip()


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
        if key:
            values[key] = _clean_value(value)
    return values


def _read_setting(name: str, file_values: dict[str, str], default: str = "") -> str:
    # .env 是本 app 的主配置入口：文件里有非空值就优先用它，
    # 避免外部 shell 注入的空/错误环境变量（如空 ANTHROPIC_API_KEY）把 .env 覆盖掉。
    # .env 未配置该项时再回退到环境变量，最后用 default。
    file_val = file_values.get(name, "")
    if file_val:
        return _normalize_pasted(file_val)
    return _normalize_pasted(os.getenv(name, default))


def _read_max_tokens(file_values: dict[str, str]) -> int:
    """CHAT_MAX_TOKENS：Anthropic 接口 max_tokens 必填，默认给大数 16384，
    避免长回答被无声截断；OpenAI 分支不使用该值。配置非法/空时回落 16384。"""
    raw = _read_setting("CHAT_MAX_TOKENS", file_values, "16384")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 16384
    return value if value > 0 else 16384


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
            max_tokens=_read_max_tokens(file_values),
        )

    return ChatConfig(
        provider="anthropic",
        api_key=_read_setting("ANTHROPIC_API_KEY", file_values),
        base_url=_read_setting("ANTHROPIC_BASE_URL", file_values),
        model=_read_setting("ANTHROPIC_MODEL", file_values, DEFAULT_MODEL) or DEFAULT_MODEL,
        max_tokens=_read_max_tokens(file_values),
    )
