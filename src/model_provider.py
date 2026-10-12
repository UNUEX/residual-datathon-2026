"""Small, explicit model transport. Keys stay in memory; redirects are forbidden."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from urllib import error, parse, request


class ProviderError(ValueError):
    pass


DEFAULT_CONFIG = {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                  "model": "qwen3:1.7b", "allow_external": False}
MAX_RESPONSE = 1024 * 1024
MIN_LOCAL_SOURCE_VERSION = (0, 40, 3)


def ollama_local_ref(model):
    """Mirror v0.40.3 source suffixes; force the server's local-only route.

    https://github.com/ollama/ollama/blob/v0.40.3/internal/modelref/modelref.go
    A remote manifest can have any alias: suffix checks alone are not sufficient.
    """
    def source_suffix(value):
        base, separator, suffix = value.rpartition(":")
        suffix = suffix.strip().casefold()
        if separator and (suffix == "cloud" or ("/" not in suffix and suffix.endswith("-cloud"))):
            return base, "cloud"
        if separator and suffix == "local":
            return base, "local"
        return value, None

    base, source = source_suffix(model.strip())
    if source == "cloud":
        raise ProviderError("Облачные модели запрещены в локальном режиме Ollama. Для внешней модели настройте HTTPS-провайдер и явно разрешите передачу вопроса.")
    if source == "local" and source_suffix(base)[1] is not None:
        raise ProviderError("Укажите один источник модели: нельзя сочетать суффиксы :local и :cloud.")
    if not base.strip():
        raise ProviderError("Укажите имя локальной модели перед :local.")
    return base.strip() + ":local"


def has_remote_metadata(item):
    return any(item.get(field) not in (None, "") for field in ("remote_host", "remote_model"))


def validate_config(value):
    if not isinstance(value, dict):
        raise ProviderError("Настройки модели должны быть объектом.")
    provider = value.get("provider", "ollama")
    if provider not in {"ollama", "openai_compatible"}:
        raise ProviderError("Поддерживаются Ollama и OpenAI-compatible API.")
    base = value.get("base_url", "")
    model = value.get("model", "")
    if not isinstance(base, str) or not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ProviderError("Укажите адрес сервера и имя модели.")
    url = parse.urlsplit(base.strip())
    try:
        port = url.port
    except ValueError:
        raise ProviderError("Некорректный порт сервера модели.") from None
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(ord(c) < 33 for c in base)):
        raise ProviderError("Адрес должен быть HTTP(S), без пароля, параметров и фрагмента.")
    host = url.hostname.lower()
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    allow = value.get("allow_external", False)
    if type(allow) is not bool:
        raise ProviderError("Разрешение внешнего сервера должно быть явно задано.")
    if not local and (not allow or url.scheme != "https"):
        raise ProviderError("Для сервера вне этого компьютера нужны HTTPS и явное разрешение передачи вопроса.")
    if provider == "ollama" and not local:
        raise ProviderError("Ollama в этом MVP подключается только на этом компьютере. Для своего HTTPS-сервера используйте совместимый API.")
    if provider == "ollama":
        ollama_local_ref(model)
    return {"provider": provider, "base_url": base.strip().rstrip("/"),
            "model": model.strip(), "allow_external": allow, "is_local": local}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Сервер модели перенаправляет запрос. Укажите конечный адрес; перенаправления запрещены.")


class ModelProvider:
    def __init__(self, config=None, api_key=""):
        self.config = validate_config(config or DEFAULT_CONFIG)
        if not isinstance(api_key, str) or len(api_key) > 4096 or "\n" in api_key or "\r" in api_key:
            raise ProviderError("Некорректный ключ API.")
        self._api_key = api_key

    def public_config(self):
        return {**self.config, "api_key_set": bool(self._api_key),
                "data_policy": "Модели передаются только ваш вопрос и описание допустимых операций. Строки таблиц и результаты расчёта не отправляются. Сам вопрос может содержать чувствительные сведения.",
                "automatic_fallback": False}

    def _request(self, endpoint, body=None, timeout=120):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.config["base_url"] + endpoint, data=data, headers=headers)
        # Disable environment proxies, including for loopback, so the chosen endpoint is exact.
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ProviderError("Ответ модели превышает допустимый размер.")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ProviderError("Сервер вернул ответ неизвестного формата.")
            return result
        except ProviderError:
            raise
        except error.HTTPError as exc:
            messages = {401: "Сервер не принял ключ API.", 403: "Сервер запретил доступ к модели.",
                        404: "Модель или адрес API не найдены. Проверьте имя модели и адрес сервера.",
                        429: "Сервер модели ограничил число запросов. Повторите позже."}
            raise ProviderError(messages.get(exc.code, "Сервер модели вернул ошибку HTTP " + str(exc.code) + ".")) from None
        except (socket.timeout, TimeoutError):
            raise ProviderError("Модель не ответила за 120 секунд. Попробуйте меньшую модель или повторите запрос.") from None
        except (error.URLError, OSError):
            raise ProviderError("Не удалось подключиться к модели. Запустите Ollama или проверьте адрес сервера.") from None
        except (ValueError, UnicodeError):
            raise ProviderError("Сервер модели вернул некорректный JSON.") from None

    def probe(self):
        ollama = self.config["provider"] == "ollama"
        result = self._request("/api/tags" if ollama else "/models", timeout=15)
        items = result.get("models" if ollama else "data", [])
        models = []
        if isinstance(items, list):
            for item in items[:1000]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name" if ollama else "id", ""))[:200]
                if ollama:
                    if has_remote_metadata(item):
                        continue
                    try:
                        ollama_local_ref(name)
                    except ProviderError:
                        continue
                if name:
                    models.append(name)
        selected = self.config["model"]
        if ollama:
            try:
                self._ollama_local_model(items)
            except ProviderError as exc:
                return {"ok": False, "models": models, "selected_model": selected,
                        "local_inference_verified": False, "message": str(exc)}
            return {"ok": True, "models": models, "selected_model": selected,
                    "local_inference_verified": True,
                    "message": "Локальная модель доступна. Запросы принудительно используют источник :local."}
        found = selected in models or (ollama and selected + ":latest" in models)
        return {"ok": found, "models": models, "selected_model": selected,
                "message": "Модель доступна." if found else "Сервер отвечает, но выбранная модель не найдена. Выберите доступную модель или загрузите её в Ollama."}

    def _ollama_local_model(self, items=None):
        """Verify before sending the question; never retry without :local."""
        local_ref = ollama_local_ref(self.config["model"])
        version = self._request("/api/version", timeout=15).get("version", "")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", str(version))
        if not match or tuple(map(int, match.groups())) < MIN_LOCAL_SOURCE_VERSION:
            raise ProviderError("Для принудительно локального запуска требуется Ollama 0.40.3 или новее. Обновите Ollama; вопрос модели не отправлен.")
        if items is None:
            items = self._request("/api/tags", timeout=15).get("models")
        if not isinstance(items, list):
            raise ProviderError("Ollama не вернул список установленных моделей. Локальное выполнение не подтверждено; вопрос не отправлен.")
        # Names in /api/tags use the canonical display name. /api/show with :local
        # also resolves full names and aliases that are not byte-identical here.
        selected = local_ref.removesuffix(":local").casefold()
        names = {selected, selected + ":latest"}
        for item in items:
            if isinstance(item, dict) and names.intersection(
                    str(item.get(key, "")).casefold() for key in ("name", "model")):
                if has_remote_metadata(item):
                    raise ProviderError("Выбранное имя Ollama ссылается на удалённую модель. В локальном режиме вопрос не отправлен.")
        try:
            info = self._request("/api/show", {"model": local_ref}, timeout=15)
        except ProviderError as exc:
            raise ProviderError("Локальная модель не подтверждена. Дождитесь загрузки модели и проверьте её имя; нужен Ollama 0.40.3 или новее. " + str(exc)) from None
        if has_remote_metadata(info):
            raise ProviderError("Метаданные Ollama указывают удалённую модель. В локальном режиме вопрос не отправлен.")
        if not isinstance(info.get("details"), dict) or not isinstance(info.get("model_info"), dict):
            raise ProviderError("Ollama вернул неполные сведения о модели. Локальное выполнение не подтверждено; вопрос не отправлен.")
        return local_ref

    def propose(self, question, system_prompt, schema):
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}]
        body = {"model": self.config["model"], "messages": messages, "stream": False}
        if self.config["provider"] == "ollama":
            body["model"] = self._ollama_local_model()
            body.update({"format": schema, "think": False,
                         "options": {"temperature": 0, "num_predict": 600, "num_ctx": 4096}})
            result = self._request("/api/chat", body)
            if has_remote_metadata(result):
                raise ProviderError("Сервер сообщил об удалённом выполнении вопреки локальному режиму. Ответ не используется; проверьте сервер Ollama.")
            message = result.get("message")
            if not isinstance(message, dict):
                raise ProviderError("Модель не вернула сообщение с планом.")
            content = message.get("content", "")
        else:
            body.update({"temperature": 0, "max_tokens": 800,
                         "response_format": {"type": "json_schema", "json_schema": {
                             "name": "research_plan", "strict": True, "schema": schema}}})
            result = self._request("/chat/completions", body)
            try:
                content = result["choices"][0]["message"]["content"]
            except (KeyError, TypeError, IndexError):
                raise ProviderError("Сервер не вернул текст плана. Нужен API с поддержкой structured outputs.") from None
        if not isinstance(content, str):
            raise ProviderError("Модель не вернула JSON-план.")
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            raise ProviderError("Модель вернула некорректный план. Расчёт не выполнялся; попробуйте уточнить вопрос.") from None
