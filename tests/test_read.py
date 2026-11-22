"""Step 2, Read: field lists, prompt, gateway, replay, orientation; no network, generated documents only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from conftest import REPO_ROOT, drawn_picture, make_catalogue, make_settings, make_text_pdf, save_picture
from fraudcheck.cli import EXIT_OK, EXIT_SETUP_ERROR, main
from fraudcheck.ingest.catalogue import family_for, load_family_rules
from fraudcheck.ingest.models import TextLayer, TextSpan
from fraudcheck.ingest.pipeline import load_record, run_ingest
from fraudcheck.read.doc_types import doc_type_for, load_doc_types
from fraudcheck.read.gateway import (
    AnswerCache,
    AzureGateway,
    GatewayAnswer,
    GatewaySetupError,
    ReadRequest,
    ReplayGateway,
    TokenLog,
    classify_error,
    missing_configuration,
)
from fraudcheck.read.models import FieldValue, PageTurn, ReadingAnswer
from fraudcheck.read.prompt import load_prompt, pdf_text_block, user_text
from fraudcheck.read.reader import build_request, read_document, run_read
from fraudcheck.read.settings import ReadSettings, load_read_settings
from fraudcheck.read.store import load_reading
from test_catalogue import NATURE_FAMILIES

DOC_TYPES = REPO_ROOT / "config" / "doc_types"
PROMPT = REPO_ROOT / "prompts" / "read_document.md"


def answer(turn: str = "0", value: str = "01/02/2026", readable: str = "yes") -> ReadingAnswer:
    return ReadingAnswer(
        nature_seen="relevé d'information", readable=readable, pages=[PageTurn(page=1, turn=turn)],
        fields=[FieldValue(name="date_edition", value=value, state="read", other_copies=[])],
        elements=[],
    )


def read_settings(root: Path, **changes) -> ReadSettings:
    values = {"root": root, "doc_types_dir": DOC_TYPES, "prompt_path": PROMPT, "model": "terra-test", "backoff_seconds": 0}
    values.update(changes)
    return ReadSettings(**values)


@pytest.fixture
def ingested(workspace):
    settings = make_settings(workspace)
    save_picture(drawn_picture((120, 80)), settings.documents_path / "DOC-1.png")
    make_text_pdf(settings.documents_path / "DOC-2.pdf")
    make_catalogue(settings.catalogue_file, [("DOC-1", "DOC-1.png", "Carte grise"),
                                             ("DOC-2", "DOC-2.pdf", "Relevé d'information (relevé de sinistres)")])
    run_ingest(settings)
    return settings


class FakeGateway:
    """Answers in order and remembers every request."""

    def __init__(self, *answers: GatewayAnswer) -> None:
        self.answers, self.requests = list(answers), []

    def ask(self, request: ReadRequest) -> GatewayAnswer:
        self.requests.append(request)
        return self.answers.pop(0)


def fake_response(parsed=None, refusal=None, finish="stop", prompt_tokens=100, completion_tokens=20):
    message = SimpleNamespace(parsed=parsed, refusal=refusal, content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))


class FakeClient:
    """Stands for openai.OpenAI: returns or raises the given items in order."""

    def __init__(self, *outcomes) -> None:
        self.outcomes, self.calls = list(outcomes), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HttpError(Exception):
    def __init__(self, status_code: int, code: str = "") -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code, self.code = status_code, code


def request(key: str = "k1") -> ReadRequest:
    return ReadRequest(doc_id="DOC-1", key=key, model="terra-test", prompt_version="v1", messages=[])


def gateway(tmp_path, client, **changes) -> tuple[AzureGateway, list[float]]:
    waits: list[float] = []
    settings = read_settings(tmp_path, **changes)
    return AzureGateway(settings, AnswerCache(tmp_path / "answers"), TokenLog(tmp_path / "tokens.jsonl"),
                        client=client, sleep=waits.append), waits


def test_every_nature_has_a_field_list_of_the_right_family() -> None:
    types = load_doc_types(DOC_TYPES)
    rules = load_family_rules(REPO_ROOT / "config" / "families.toml")
    for nature in [*NATURE_FAMILIES, "Permis de conduire étranger, recto", "Permis de conduire ancien modèle, verso"]:
        doc_type = doc_type_for(nature, types)
        assert doc_type is not None, nature
        assert doc_type.family == family_for(nature, rules)
    assert doc_type_for("Facture d'électricité", types) is None
    releve = doc_type_for("RELEVE D'INFORMATION (RELEVE DE SINISTRES)", types)
    assert {"date_edition", "date_souscription", "situation_contrat", "bonus_malus", "vehicule"} <= set(releve.field_names())


def test_prompt_parts_version_and_filling(tmp_path) -> None:
    prompt = load_prompt(PROMPT)
    assert "transcripteur" in prompt.system and "corrige" in prompt.system
    types = load_doc_types(DOC_TYPES)
    layer = TextLayer(id="DOC-1", spans=[
        TextSpan(page=1, text="seconde", font="F", size=9, color="#000000", bbox=(50, 40, 90, 50)),
        TextSpan(page=1, text="Première", font="F", size=9, color="#000000", bbox=(10, 10, 40, 20)),
        TextSpan(page=1, text="ligne", font="F", size=9, color="#000000", bbox=(45, 11, 70, 21)),
    ])
    text = user_text(prompt, "Carte grise", doc_type_for("Carte grise", types), types, layer, 2, 1000)
    assert "Carte grise" in text and "genre" in text and "Les 2 images" in text
    assert "Première ligne\nseconde" in text
    unknown = user_text(prompt, None, None, types, None, 1, 1000)
    assert "n'est pas connue" in unknown and "Relevé d'identité bancaire" in unknown and "texte réel" in unknown
    assert pdf_text_block(layer, 10).endswith("coupé ...]")
    changed = tmp_path / "prompt.md"
    changed.write_text(PROMPT.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert load_prompt(changed).version != prompt.version


def test_request_carries_all_pages_text_and_a_stable_key(ingested) -> None:
    settings = read_settings(ingested.root)
    prompt, types = load_prompt(PROMPT), load_doc_types(DOC_TYPES)
    record = load_record(ingested, "DOC-2")
    first = build_request(record, ingested, settings, prompt, doc_type_for(record.nature, types), types, "terra-test", {})
    content = first.messages[1]["content"]
    assert first.messages[0]["role"] == "system" and content[0]["type"] == "text"
    assert "Texte contenu dans le fichier PDF" in content[0]["text"]
    assert [part["image_url"]["url"][:22] for part in content[1:]] == ["data:image/png;base64,"]
    again = build_request(record, ingested, settings, prompt, doc_type_for(record.nature, types), types, "terra-test", {})
    other_model = build_request(record, ingested, settings, prompt, doc_type_for(record.nature, types), types, "sol", {})
    assert first.key == again.key != other_model.key


def test_sideways_pages_are_turned_and_read_once_more(ingested) -> None:
    settings = read_settings(ingested.root)
    prompt, types = load_prompt(PROMPT), load_doc_types(DOC_TYPES)
    record = load_record(ingested, "DOC-1")
    fake = FakeGateway(GatewayAnswer("read", answer(turn="90")), GatewayAnswer("read", answer(turn="0")))
    reading, answers = read_document(record, ingested, settings, prompt, types, fake, "terra-test")
    assert len(fake.requests) == 2 and reading.turns == {1: 90} and reading.calls == 2
    assert fake.requests[0].key != fake.requests[1].key
    turned = ingested.cache_path / "DOC-1" / "turned-1-90.png"
    assert Image.open(turned).size == (80, 120)  # the 120 x 80 picture, turned a quarter


def given_up_but_copied() -> ReadingAnswer:
    """The reader could not read the value at its main place, yet copied it from another place of the document."""
    return ReadingAnswer(
        nature_seen="relevé d'information", readable="partly", pages=[PageTurn(page=1, turn="0")],
        fields=[FieldValue(name="date_edition", value=None, state="unreadable", other_copies=["01/02/2026"])],
        elements=[],
    )


def test_a_reading_in_doubt_gets_one_reading_by_the_stronger_deployment(ingested) -> None:
    prompt, types = load_prompt(PROMPT), load_doc_types(DOC_TYPES)
    record = load_record(ingested, "DOC-1")
    with_stronger = read_settings(ingested.root, strong_model="sol-test")
    fake = FakeGateway(GatewayAnswer("read", given_up_but_copied()), GatewayAnswer("read", answer(value="03/02/2026")))
    reading, answers = read_document(record, ingested, with_stronger, prompt, types, fake, "terra-test")
    assert [asked.model for asked in fake.requests] == ["terra-test", "sol-test"] and len(answers) == 2
    assert reading.model == "sol-test" and reading.calls == 2 and reading.field("date_edition").state == "read"
    # one call only: when nothing is in doubt, when no stronger deployment is set, when the setting is off
    for changes, first in (({"strong_model": "sol-test"}, answer()), ({}, given_up_but_copied()),
                           ({"strong_model": "sol-test", "second_opinion": False}, given_up_but_copied())):
        fake = FakeGateway(GatewayAnswer("read", first))
        reading, _ = read_document(record, ingested, read_settings(ingested.root, **changes), prompt, types, fake, "terra-test")
        assert len(fake.requests) == 1 and reading.model == "terra-test"
    # a second opinion that fails changes nothing; the stronger deployment is never asked for a second opinion on itself
    fake = FakeGateway(GatewayAnswer("read", given_up_but_copied()), GatewayAnswer("no_reading", reason="refusal"))
    reading, _ = read_document(record, ingested, with_stronger, prompt, types, fake, "terra-test")
    assert reading.model == "terra-test" and reading.field("date_edition").state == "unreadable" and reading.calls == 2
    fake = FakeGateway(GatewayAnswer("read", given_up_but_copied()))
    reading, _ = read_document(record, ingested, with_stronger, prompt, types, fake, "sol-test")
    assert len(fake.requests) == 1


def test_gateway_reads_caches_and_logs_tokens_without_content(tmp_path) -> None:
    client = FakeClient(fake_response(parsed=answer(value="Secret 1234")))
    live, _ = gateway(tmp_path, client)
    first = live.ask(request())
    assert first.status == "read" and first.input_tokens == 100 and first.calls == 1
    assert client.calls[0]["response_format"] is ReadingAnswer and client.calls[0]["max_completion_tokens"] == 8000
    assert live.ask(request()).cached and len(client.calls) == 1  # never paid twice
    log = (tmp_path / "tokens.jsonl").read_text(encoding="utf-8")
    assert json.loads(log.splitlines()[0])["input_tokens"] == 100 and "Secret" not in log and "1234" not in log


def test_rate_limits_are_retried_with_a_growing_wait(tmp_path) -> None:
    client = FakeClient(HttpError(429), HttpError(503), fake_response(parsed=answer()))
    live, waits = gateway(tmp_path, client, backoff_seconds=1.0)
    result = live.ask(request())
    assert result.status == "read" and result.calls == 3 and len(waits) == 2 and waits[1] > waits[0]


@pytest.mark.parametrize(
    ("outcomes", "reason"),
    [
        ([fake_response(refusal="I cannot help"), fake_response(refusal="I cannot help")], "refusal"),
        ([fake_response(parsed=None), fake_response(parsed=None)], "empty answer"),
        ([HttpError(400, "content_filter")], "content filter"),
        ([fake_response(finish="length"), fake_response(finish="length")], "answer cut off"),
        ([HttpError(429)] * 5, "call failed (HttpError, HTTP 429)"),
    ],
)
def test_failures_mean_no_reading_never_suspicion(tmp_path, outcomes, reason) -> None:
    live, _ = gateway(tmp_path, FakeClient(*outcomes))
    result = live.ask(request())
    assert result.status == "no_reading" and result.reason == reason and result.answer is None
    assert not (tmp_path / "answers" / "k1.json").exists()  # failures are not kept: a later run asks again


def test_a_cut_off_answer_is_tried_once_more_with_twice_the_room(tmp_path) -> None:
    client = FakeClient(fake_response(finish="length"), fake_response(parsed=answer()))
    live, _ = gateway(tmp_path, client)
    result = live.ask(request())
    assert result.status == "read" and result.calls == 2 and result.input_tokens == 200
    assert [call["max_completion_tokens"] for call in client.calls] == [8000, 16000]


def test_the_run_stops_after_three_failed_calls_in_a_row(tmp_path) -> None:
    """A spent quota or a dead network must not fail slowly on every document left."""
    live, _ = gateway(tmp_path, FakeClient(*[HttpError(429)] * 15))
    assert live.ask(request("a")).reason == "call failed (HttpError, HTTP 429)"
    assert live.ask(request("b")).status == "no_reading"
    with pytest.raises(GatewaySetupError, match="3 calls in a row failed.*HTTP 429"):
        live.ask(request("c"))


def test_a_success_between_failures_resets_the_count(tmp_path) -> None:
    outcomes = [*[HttpError(500)] * 5, fake_response(parsed=answer()), *[HttpError(500)] * 10]
    live, _ = gateway(tmp_path, FakeClient(*outcomes))
    assert live.ask(request("a")).status == "no_reading" and live.ask(request("b")).status == "read"
    assert live.ask(request("c")).status == "no_reading" and live.ask(request("d")).status == "no_reading"


def test_a_paid_answer_is_kept_even_when_the_token_log_cannot_be_written(tmp_path) -> None:
    live, _ = gateway(tmp_path, FakeClient(fake_response(parsed=answer())))

    def locked(*_args, **_kwargs):
        raise PermissionError("held by another program")

    live.token_log.write = locked
    assert live.ask(request()).status == "read" and (tmp_path / "answers" / "k1.json").exists()


def test_a_missing_sdk_is_said_plainly(tmp_path) -> None:
    class NoSdk:
        @property
        def chat(self):
            raise GatewaySetupError("the 'openai' package is not installed: pip install -r requirements.txt")

    live, _ = gateway(tmp_path, NoSdk())
    with pytest.raises(GatewaySetupError, match="pip install"):
        live.ask(request())


def test_refused_credentials_stop_the_run(tmp_path) -> None:
    live, _ = gateway(tmp_path, FakeClient(HttpError(401)))
    with pytest.raises(GatewaySetupError):
        live.ask(request())
    assert classify_error(HttpError(404)) == "auth" and classify_error(TimeoutError()) == "retry"


@pytest.mark.parametrize(("status", "cause"), [(401, "identifiers"), (403, "network"), (404, "deployment name")])
def test_a_refused_call_names_its_likely_cause(tmp_path, status, cause) -> None:
    live, _ = gateway(tmp_path, FakeClient(HttpError(status)))
    with pytest.raises(GatewaySetupError, match=cause):
        live.ask(request())


def test_replay_uses_saved_answers_only(ingested) -> None:
    settings = read_settings(ingested.root)
    prompt, types = load_prompt(PROMPT), load_doc_types(DOC_TYPES)
    cache = AnswerCache(ingested.cache_path / "read" / "answers")
    record = load_record(ingested, "DOC-2")
    key_request = build_request(record, ingested, settings, prompt, doc_type_for(record.nature, types), types, "terra-test", {})
    cache.put(key_request, GatewayAnswer("read", answer(), input_tokens=900, output_tokens=80))
    run = run_read(ingested, settings, replay=True)
    assert [reading.id for reading in run.readings] == ["DOC-2"] and run.not_read == ["DOC-1"]
    assert run.live_calls == 0 and run.from_cache == 1
    assert load_reading(ingested, "DOC-2").field("date_edition").value == "01/02/2026"
    assert load_reading(ingested, "DOC-1") is None  # nothing saved for a document not read
    assert ReplayGateway(cache).ask(request("missing")).reason == "not in the replay cache"


def test_live_run_needs_configuration(ingested, tmp_path) -> None:
    settings = read_settings(ingested.root, endpoint=None)
    with pytest.raises(GatewaySetupError, match="AZURE_OPENAI_ENDPOINT"):
        run_read(ingested, settings)
    (tmp_path / ".env").write_text("AZURE_OPENAI_API_KEY=not-a-real-key\nAZURE_OPENAI_MODEL=terra\n", encoding="utf-8")
    loaded = load_read_settings({"root": tmp_path}, environ={})
    assert loaded.model == "terra" and missing_configuration(loaded) == ["AZURE_OPENAI_ENDPOINT"]


def test_no_test_can_read_the_team_s_real_identifiers() -> None:
    """The repository's own .env is hidden from every test (conftest), so no test can call the paid service."""
    from fraudcheck.read import gateway

    assert not any(gateway.secrets(REPO_ROOT).values())
    assert gateway.missing_configuration(load_read_settings())


def test_only_the_gateway_imports_the_azure_libraries() -> None:
    for path in (REPO_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+(openai|azure)\b", text, re.M):
            assert path.name == "gateway.py", path


def test_read_command_without_configuration_and_in_replay(ingested, monkeypatch, capsys) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("READ_DOC_TYPES_DIR", str(DOC_TYPES))
    monkeypatch.setenv("READ_PROMPT_PATH", str(PROMPT))
    monkeypatch.setenv("READ_ROOT", str(ingested.root))
    monkeypatch.setenv("INGEST_ROOT", str(ingested.root))
    monkeypatch.setenv("INGEST_DOCUMENTS_DIR", "data/documents")
    assert main(["read", "--limit", "1"]) == EXIT_SETUP_ERROR
    assert "not configured" in capsys.readouterr().err
    assert main(["read", "--replay"]) == EXIT_OK
    assert "not read   2 documents" in capsys.readouterr().out


def test_real_sdk_structured_output_through_a_simulated_transport(tmp_path) -> None:
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    sent = {}

    def handler(http_request):
        sent["body"] = json.loads(http_request.content)
        body = {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer().model_dump_json(), "refusal": None}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        return httpx.Response(200, json=body)

    client = openai.OpenAI(base_url="https://example.invalid/openai/v1/", api_key="test", max_retries=0,
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    live, _ = gateway(tmp_path, client)
    assert live.ask(request()).answer.fields[0].value == "01/02/2026"
    assert sent["body"]["response_format"]["json_schema"]["strict"] is True
