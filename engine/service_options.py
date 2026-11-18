"""Model and proxy settings shared by the translation engine and Windows backend."""

from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_REQUIRED_LEVELS = ("low", "medium", "high", "xhigh", "max")
_OPTIONAL_LEVELS = ("none", *_REQUIRED_LEVELS)
_OPENAI_REASONING_LEVELS = {
    "gpt-6.2-astra": _REQUIRED_LEVELS,
    "gpt-6.2-sol": _REQUIRED_LEVELS,
    "gpt-6.1-sol": _REQUIRED_LEVELS,
    "gpt-6-astra": _REQUIRED_LEVELS,
    "gpt-6.1-terra": _OPTIONAL_LEVELS,
    "gpt-6-sol": _OPTIONAL_LEVELS,
    "gpt-6-luna": _OPTIONAL_LEVELS,
}


class ServiceOptionError(ValueError):
    """A fixed validation message for a model or network setting."""


def openai_reasoning_effort(provider, model, effort="auto"):
    known = _OPENAI_REASONING_LEVELS.get(model) if provider == "openAI" else None
    if effort == "auto":
        # Unknown models use the provider's default.
        return known[0] if known else None
    if provider != "openAI" or effort not in (known or _OPTIONAL_LEVELS):
        raise ServiceOptionError("当前模型不支持此推理强度，请选择“自动”或其他可用强度。")
    return effort


def proxy_settings(service, *, local):
    mode = service.get("proxyMode", "system")
    if mode not in ("system", "direct", "manual"):
        raise ServiceOptionError("代理模式无效。")
    if local or mode != "manual":
        return "system" if local else mode, ""

    address = service.get("proxyURL")
    message = "请输入 HTTP 代理地址和端口（如 http://127.0.0.1:7890），地址中不要包含用户名或密码。"
    if not isinstance(address, str):
        raise ServiceOptionError(message)
    address = address.strip()
    if len(address) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "\\?#" for c in address):
        raise ServiceOptionError(message)
    try:
        proxy = urlsplit(address)
        port = proxy.port
    except ValueError:
        raise ServiceOptionError(message) from None
    if (proxy.scheme != "http" or not proxy.hostname or not port or proxy.username is not None
            or proxy.password is not None or proxy.path not in ("", "/")):
        raise ServiceOptionError(message)
    return mode, "http://" + proxy.netloc
