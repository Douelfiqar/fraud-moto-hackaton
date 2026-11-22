"""The gateway: the only code of the project that calls Azure OpenAI.

- It connects as ``exemples/appel_azure.py`` does: with the API key when
  ``AZURE_OPENAI_API_KEY`` is set, otherwise with the team's Entra identifiers.
- It asks for a structured output: the model must fill the form the request names
  (the reading's form, or the inspection's).
- It waits and tries again after a rate limit (HTTP 429) or a passing network error,
  the wait doubling each time; a refusal or an empty answer is tried once more; an
  answer cut off by the token limit is tried once more with twice the room.
- A refused call (401, 403, 404) stops the run with a message naming the likely cause.
- It writes the tokens of every call, never the content, to ``cache/read/tokens.jsonl``.
- It keeps every answer on disk, keyed by the document's fingerprint, the prompt
  version and the model, so the same question is never paid for twice.
- In replay mode it answers only from that disk cache and never touches the network.

A refusal, an empty answer or a content-filter block means "no reading", never suspicion.
Secrets are read from the environment or ``.env`` when the client is built, and are
never printed, logged or stored.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from fraudcheck.config import ENV_FILE, read_dotenv
from fraudcheck.ingest.storage import atomic_write_text
from fraudcheck.read.models import ReadingAnswer
from fraudcheck.read.settings import ReadSettings

log = logging.getLogger(__name__)

SECRET_NAMES = ("AZURE_OPENAI_API_KEY", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
ENTRA_NAMES = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
ANSWERS_FOLDER = Path("read") / "answers"
TOKEN_LOG = Path("read") / "tokens.jsonl"
_MAX_WAIT_S = 60.0
#: Calls failing one after the other for a reason about the run (quota spent, network down, request
#: refused): past this many, the run stops instead of failing, slowly, on every document left.
_MAX_FAILURES_IN_A_ROW = 3
#: Reasons about the document itself: they are kept. Other failures are about the run.
#: An answer cut off by the token limit is about the run (the limit), not the document: an earlier
#: reading of that document is kept.
DOCUMENT_REASONS = {"refusal", "empty answer", "content filter", "answer did not match the form"}
_TRY_AGAIN_ONCE = {"refusal", "empty answer"}


class GatewaySetupError(RuntimeError):
    """Azure cannot be used: not configured, ``openai`` not installed, or credentials refused."""


def failure_reason(error: Exception) -> str:
    """'call failed (BadRequestError, HTTP 400, code invalid_request)': what failed, never the service's
    message, which can quote the request."""
    parts = [type(error).__name__]
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", code):
        parts.append(f"code {code}")
    return "call failed (" + ", ".join(parts) + ")"


@dataclass
class ReadRequest:
    doc_id: str
    key: str
    model: str
    prompt_version: str
    messages: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]]  # a function: built only when a call is made
    schema: type[BaseModel] = ReadingAnswer  # the form the model must fill
    stage: str = "read"  # "read" or "inspect", written in the token log

    def payload(self) -> list[dict[str, Any]]:
        """The messages, built now when they were given as a function (answers from the cache never build them)."""
        if callable(self.messages):
            self.messages = self.messages()
        return self.messages


@dataclass
class GatewayAnswer:
    status: Literal["read", "no_reading"]
    answer: Any = None  # an instance of the request's schema
    reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    calls: int = 0


def no_reading(reason: str) -> GatewayAnswer:
    return GatewayAnswer(status="no_reading", reason=reason)


class Gateway(Protocol):
    def ask(self, request: ReadRequest) -> GatewayAnswer: ...


class AnswerCache:
    """Answers saved on disk, one JSON file per question."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def path(self, key: str) -> Path:
        return self.folder / f"{key}.json"

    def get(self, key: str, schema: type[BaseModel] = ReadingAnswer) -> GatewayAnswer | None:
        try:
            data = json.loads(self.path(key).read_text(encoding="utf-8"))
            answer = schema.model_validate(data["answer"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            return None
        return GatewayAnswer(status="read", answer=answer, input_tokens=data.get("input_tokens", 0),
                             output_tokens=data.get("output_tokens", 0), cached=True)

    def put(self, request: ReadRequest, answer: GatewayAnswer) -> None:
        payload = {
            "doc_id": request.doc_id, "stage": request.stage, "schema": request.schema.__name__, "model": request.model,
            "prompt_version": request.prompt_version, "saved_at": datetime.now(UTC).isoformat(), "input_tokens": answer.input_tokens,
            "output_tokens": answer.output_tokens, "answer": answer.answer.model_dump(mode="json") if answer.answer else None,
        }
        atomic_write_text(self.path(request.key), json.dumps(payload, ensure_ascii=False, indent=1))


class TokenLog:
    """One line per question asked to Azure: tokens and outcome, never the content."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, request: ReadRequest, answer: GatewayAnswer, seconds: float) -> None:
        entry = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"), "stage": request.stage, "id": request.doc_id,
            "model": request.model,
            "prompt_version": request.prompt_version, "status": answer.status, "reason": answer.reason,
            "calls": answer.calls, "input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens,
            "seconds": round(seconds, 2),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def secrets(root: Path) -> dict[str, str]:
    """The API key or Entra identifiers, from the environment first, then from ``.env``."""
    values = {name: os.environ.get(name, "").strip() for name in SECRET_NAMES}
    dotenv = read_dotenv(root / ENV_FILE)
    for name in SECRET_NAMES:
        values[name] = values[name] or dotenv.get(name, "").strip()
    return values


def missing_configuration(settings: ReadSettings) -> list[str]:
    """Names of the ``.env`` entries still needed for a live run (empty when Azure can be called)."""
    missing = []
    if not settings.endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not settings.deployment:
        missing.append("AZURE_OPENAI_MODEL_SOL" if settings.use_strong else "AZURE_OPENAI_MODEL")
    values = secrets(settings.root)
    if not values["AZURE_OPENAI_API_KEY"] and not all(values[name] for name in ENTRA_NAMES):
        missing.append("AZURE_OPENAI_API_KEY, or AZURE_TENANT_ID + AZURE_CLIENT_ID + AZURE_CLIENT_SECRET")
    return missing


def build_client(settings: ReadSettings) -> Any:
    """An OpenAI client on the team's Azure endpoint, as in ``exemples/appel_azure.py``."""
    missing = missing_configuration(settings)
    if missing:
        raise GatewaySetupError("Azure is not configured; missing in .env: " + "; ".join(missing))
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GatewaySetupError("the 'openai' package is not installed: pip install -r requirements.txt") from exc
    endpoint = (settings.endpoint or "").rstrip("/").removesuffix("/openai/v1")
    values = secrets(settings.root)
    if values["AZURE_OPENAI_API_KEY"]:
        api_key: Any = values["AZURE_OPENAI_API_KEY"]
    else:
        try:
            from azure.identity import ClientSecretCredential, get_bearer_token_provider
        except ImportError as exc:
            raise GatewaySetupError("the 'azure-identity' package is not installed: pip install -r requirements.txt") from exc
        credential = ClientSecretCredential(*(values[name] for name in ENTRA_NAMES))
        api_key = get_bearer_token_provider(credential, TOKEN_SCOPE)  # the SDK renews the token itself
    return OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=api_key, max_retries=0, timeout=settings.timeout_seconds)


def classify_error(error: Exception) -> Literal["retry", "filter", "cut", "auth", "fatal"]:
    """What to do after a failed call, judged from the error's type and HTTP status."""
    name = type(error).__name__
    status = getattr(error, "status_code", None)
    code = str(getattr(error, "code", "") or "")
    if "ContentFilter" in name or code == "content_filter":
        return "filter"
    if "LengthFinishReason" in name:
        return "cut"
    if status in (401, 403, 404) or name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        return "auth"
    if status == 429 or "RateLimit" in name:
        return "retry"
    if status in (408, 409) or (isinstance(status, int) and status >= 500) or "Timeout" in name or "Connection" in name:
        return "retry"
    return "fatal"


def refusal_message(error: Exception) -> str:
    """Say what a refused call most likely means, so the fix is obvious on the day.

    The organisers' brief gives the usual causes: 401 for wrong credentials, 403 for a
    network that is not allowed, 404 for a wrong deployment name.
    """
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    if status == 403 or name == "PermissionDeniedError":
        hint = "this network is not allowed (Azure only answers from APRIL's network), or the account lacks the right"
    elif status == 404 or name == "NotFoundError":
        hint = ("the deployment name is wrong: with Entra identifiers use the team's full deployment name, "
                "with an API key the short one")
    else:
        hint = "the API key or the Entra identifiers are wrong or have expired"
    return f"Azure refused the call ({name}, HTTP {status}): {hint}. Check .env."


def interpret(response: Any, schema: type[BaseModel] = ReadingAnswer) -> GatewayAnswer:
    """Turn the SDK's response into a filled form, or into "no reading" with its reason."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return no_reading("empty answer")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if getattr(message, "refusal", None):
        return no_reading("refusal")
    finish = getattr(choice, "finish_reason", None)
    if finish == "content_filter":
        return no_reading("content filter")
    if finish == "length":
        return no_reading("answer cut off")
    parsed = getattr(message, "parsed", None)
    try:
        if parsed is None and getattr(message, "content", None):
            parsed = schema.model_validate_json(message.content)
        if parsed is None:
            return no_reading("empty answer")
        answer = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
    except (ValueError, ValidationError):
        return no_reading("answer did not match the form")
    return GatewayAnswer(status="read", answer=answer)


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


class AzureGateway:
    """Live calls, with the disk cache in front."""

    def __init__(
        self,
        settings: ReadSettings,
        cache: AnswerCache,
        token_log: TokenLog,
        *,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        force: bool = False,
    ) -> None:
        self.settings, self.cache, self.token_log = settings, cache, token_log
        self._client, self.sleep, self.force = client, sleep, force
        self._failures_in_a_row = 0

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_client(self.settings)
        return self._client

    def ask(self, request: ReadRequest) -> GatewayAnswer:
        if not self.force and (saved := self.cache.get(request.key, request.schema)) is not None:
            return saved
        started = time.perf_counter()
        calls = tokens_in = tokens_out = 0
        soft_failures = 0
        cap, enlarged = self.settings.max_completion_tokens, False
        answer = no_reading("no call made")
        for attempt in range(self.settings.max_retries + 1):
            calls += 1
            try:
                response = self._parse(request, cap)
            except GatewaySetupError:
                raise  # not configured, or the SDK is missing: say so rather than count a failed call
            except Exception as exc:  # the SDK raises many types; each is classified below
                kind = classify_error(exc)
                if kind == "auth":
                    raise GatewaySetupError(refusal_message(exc)) from None
                if kind == "retry" and attempt < self.settings.max_retries:
                    self.sleep(self._wait(exc, attempt))
                    continue
                if kind == "cut":
                    used_in, used_out = _usage(getattr(exc, "completion", None))
                    tokens_in, tokens_out = tokens_in + used_in, tokens_out + used_out
                    if not enlarged and attempt < self.settings.max_retries:
                        cap, enlarged = cap * 2, True  # the form did not fit: one more try with twice the room
                        continue
                answer = {"filter": no_reading("content filter"), "cut": no_reading("answer cut off")}.get(
                    kind, no_reading(failure_reason(exc))
                )
                break
            used_in, used_out = _usage(response)
            tokens_in, tokens_out = tokens_in + used_in, tokens_out + used_out
            answer = interpret(response, request.schema)
            if answer.reason == "answer cut off" and not enlarged and attempt < self.settings.max_retries:
                cap, enlarged = cap * 2, True  # a long form, or a model that reasons before answering
                continue
            if answer.reason in _TRY_AGAIN_ONCE and soft_failures == 0 and attempt < self.settings.max_retries:
                soft_failures += 1
                continue
            break
        answer.calls, answer.input_tokens, answer.output_tokens = calls, tokens_in, tokens_out
        if answer.status == "read":
            self.cache.put(request, answer)  # first: this answer is paid for, nothing after may lose it
        try:
            self.token_log.write(request, answer, time.perf_counter() - started)
        except OSError as exc:  # the log held by another program: the run and the answer matter more
            log.warning("token log not written for %s (%s)", request.doc_id, type(exc).__name__)
        self._stop_after_repeated_failures(answer)
        return answer

    def _stop_after_repeated_failures(self, answer: GatewayAnswer) -> None:
        if not (answer.reason or "").startswith("call failed"):
            self._failures_in_a_row = 0
            return
        self._failures_in_a_row += 1
        if self._failures_in_a_row >= _MAX_FAILURES_IN_A_ROW:
            raise GatewaySetupError(
                f"{self._failures_in_a_row} calls in a row failed, the last one: {answer.reason}. The run stops here "
                "rather than fail on every document left. Check the network, the quota and the size of the request, "
                "then run again: the answers already received are kept."
            )

    def _parse(self, request: ReadRequest, max_completion_tokens: int) -> Any:
        completions = self.client.chat.completions
        parse = getattr(completions, "parse", None) or self.client.beta.chat.completions.parse
        return parse(
            model=request.model,
            messages=request.payload(),
            response_format=request.schema,
            max_completion_tokens=max_completion_tokens,
        )

    def _wait(self, error: Exception, attempt: int) -> float:
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        try:
            hinted = float(headers.get("retry-after", ""))
        except (TypeError, ValueError):
            hinted = 0.0
        if hinted > 0:
            return min(hinted, _MAX_WAIT_S)
        return min(self.settings.backoff_seconds * 2**attempt + random.uniform(0, 0.5), _MAX_WAIT_S)


class ReplayGateway:
    """Answers only from the disk cache: no network, no cost. Missing answers are "no reading"."""

    def __init__(self, cache: AnswerCache) -> None:
        self.cache = cache

    def ask(self, request: ReadRequest) -> GatewayAnswer:
        return self.cache.get(request.key, request.schema) or no_reading("not in the replay cache")
