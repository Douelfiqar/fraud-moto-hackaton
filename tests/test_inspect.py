"""Stage "inspect": base forms, prompt, tiles, references, gateway, replay and the four checks; no network.

Every document here is drawn by the tests; every answer of the model is written by hand.
"""

from __future__ import annotations

import base64
import io
import json
import shutil

import pytest
from PIL import Image

from conftest import REPO_ROOT, drawn_picture, make_catalogue, make_settings, save_picture
from fraudcheck.checks.base import DocumentContext, registered
from fraudcheck.checks.runner import check_document, document_context
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.cli import EXIT_OK, EXIT_SETUP_ERROR, main
from fraudcheck.ingest.pipeline import load_record, run_ingest
from fraudcheck.inspect.inspector import inspect_document, page_turns, run_inspect, tile_boxes, tile_caption
from fraudcheck.inspect.models import (
    FieldOrder,
    FinePrint,
    FixedTextError,
    Inspection,
    InspectionAnswer,
    TypographyObservation,
    ZoneObservation,
)
from fraudcheck.inspect.prompt import NO_FORM, load_prompt, user_text
from fraudcheck.inspect.settings import InspectSettings
from fraudcheck.inspect.store import load_inspection, save_inspection
from fraudcheck.read.doc_types import doc_type_named, load_doc_types
from fraudcheck.read.gateway import AnswerCache, AzureGateway, GatewayAnswer, ReadRequest, TokenLog
from fraudcheck.read.models import Reading
from test_read import FakeClient, FakeGateway, fake_response, read_settings

DOC_TYPES = REPO_ROOT / "config" / "doc_types"
PROMPT = REPO_ROOT / "prompts" / "inspect_document.md"
TYPES = load_doc_types(DOC_TYPES)
INSPECTION_CHECKS = {"base_form", "fixed_text_spelling", "field_order", "typography_picture"}
INVENTED = "VALEUR INVENTEE 123"  # stands for a value of the document, which must never reach the evidence


def inspect_settings(root, **changes) -> InspectSettings:
    values = {"root": root, "doc_types_dir": DOC_TYPES, "prompt_path": PROMPT}
    values.update(changes)
    return InspectSettings(**values)


def answer(**changes) -> InspectionAnswer:
    values = {"zones": [], "fixed_text_errors": [], "field_order": FieldOrder(verdict="cannot_tell", swapped=[]),
              "typography": [], "fine_print": [], "capture": "scan", "readable": "yes"}
    values.update(changes)
    return InspectionAnswer(**values)


@pytest.fixture
def ingested(workspace):
    settings = make_settings(workspace)
    save_picture(drawn_picture((120, 80)), settings.documents_path / "DOC-1.png")
    save_picture(drawn_picture((90, 60)), settings.documents_path / "DOC-2.png")
    make_catalogue(settings.catalogue_file, [("DOC-1", "DOC-1.png", "Permis de conduire nouveau modèle, recto"),
                                             ("DOC-2", "DOC-2.png", "Relevé d'identité bancaire")])
    run_ingest(settings)
    return settings


def judged(ingested, doc_id: str, inspected: InspectionAnswer | None, record=None, **changes) -> dict:
    """The inspection checks on one document whose inspection holds ``inspected``."""
    record = record or load_record(ingested, doc_id)
    values = {"id": doc_id, "status": "inspected" if inspected else "no_inspection", "answer": inspected,
              "fingerprint": record.fingerprint}
    values.update(changes)
    context = DocumentContext(record=record, reading=None, text_layer=None, inspection=Inspection(**values),
                              settings=CheckSettings(root=ingested.root, doc_types_dir=DOC_TYPES))
    return check_document(context, {name: check for name, check in registered().items() if name in INSPECTION_CHECKS})


def pictures_sent(request: ReadRequest) -> list[dict]:
    return [part for part in request.payload()[1]["content"] if part["type"] == "image_url"]


# --- base forms and prompt -------------------------------------------------------------------------------------


def test_every_type_has_a_base_form_with_a_source_for_each_item() -> None:
    for kind in TYPES:
        form = kind.base_form
        assert form is not None and form.description and form.source, kind.name
        assert all(item.source for item in [*form.labels, *form.zones]), kind.name
        assert len(set(form.field_order)) == len(form.field_order), kind.name
    releve = doc_type_named("releve", TYPES).base_form
    assert releve.field_order == [*(f"Section {letter}" for letter in "ABCDEF"), "Date et signature"]
    assert {kind.name for kind in TYPES if kind.base_form.layout == "fixed"} == {
        "carte_grise", "permis_nouveau_recto", "permis_nouveau_verso", "piece_identite", "titre_sejour_recto",
        "titre_sejour_verso"}


def test_the_answer_form_is_strict() -> None:
    schema = InspectionAnswer.model_json_schema()
    for shape in [schema, *schema["$defs"].values()]:
        if shape.get("type") == "object":
            assert shape["additionalProperties"] is False and set(shape["required"]) == set(shape["properties"])


def test_the_prompt_describes_the_base_form_and_fills_every_place() -> None:
    prompt = load_prompt(PROMPT)
    kind = doc_type_named("permis_nouveau_recto", TYPES)
    text = user_text(prompt, kind.natures[0], kind, pages=1, tiles=4, references=2)
    assert "$" not in text and "- Photo du titulaire (rubrique 6) ; place : à gauche" in text
    assert "3. ; 4a. ; 5." in text and "2 image(s) de référence" in text and "4 zoom(s)" in text
    assert "jamais les valeurs" in prompt.system and "pas des anomalies" in prompt.system
    unknown = user_text(prompt, None, None, pages=2, tiles=0, references=0)
    assert NO_FORM in unknown and "référence" not in unknown


def test_tiles_cover_the_page_and_overlap() -> None:
    boxes = [box for _, _, box in tile_boxes(1000, 600, 2, 3, 0.1)]
    assert len(boxes) == 6 and boxes[0][:2] == (0, 0)
    assert max(box[2] for box in boxes) == 1000 and max(box[3] for box in boxes) == 600
    assert boxes[1][0] < boxes[0][2] and boxes[3][1] < boxes[0][3]  # neighbours share a strip
    assert tile_caption(1, 0, 1, 2, 2) == "Page 1, zoom : partie haut droite"
    assert tile_caption(2, 0, 2, 1, 3) == "Page 2, zoom : partie droite"


# --- what is sent -----------------------------------------------------------------------------------------------


def test_one_request_sends_the_page_its_tiles_and_a_reference(ingested) -> None:
    save_picture(drawn_picture((300, 200)), ingested.root / "data" / "templates" / "permis_nouveau_recto" / "modele.png")
    record = load_record(ingested, "DOC-1")
    fake = FakeGateway(GatewayAnswer("read", answer()))
    inspection, _, refused = inspect_document(record, ingested, inspect_settings(ingested.root), load_prompt(PROMPT),
                                              TYPES, fake, "terra-test")
    request = fake.requests[0]
    assert request.stage == "inspect" and request.schema is InspectionAnswer and callable(request.messages)
    pictures = pictures_sent(request)
    assert len(pictures) == 1 + 4 + 1  # the whole page, a 2 x 2 grid of tiles, one reference
    assert [p["image_url"]["detail"] for p in pictures] == ["high"] * 5 + ["low"]
    assert inspection.status == "inspected" and inspection.tiles == 4 and inspection.references == 1 and refused == 0


def test_tiles_stop_at_the_cap_and_references_are_optional(ingested) -> None:
    record, prompt = load_record(ingested, "DOC-1"), load_prompt(PROMPT)

    def sent(**changes):
        fake = FakeGateway(GatewayAnswer("read", answer()))
        inspection, _, _ = inspect_document(record, ingested, inspect_settings(ingested.root, **changes), prompt, TYPES,
                                            fake, "terra-test")
        return fake.requests[0], inspection

    missing_request, missing = sent()
    folder = ingested.root / "data" / "templates" / "permis_nouveau_recto"
    folder.mkdir(parents=True)
    empty_request, empty = sent()
    save_picture(drawn_picture((300, 200)), folder / "modele.jpg")
    filled_request, filled = sent()
    assert missing.references == empty.references == 0 and filled.references == 1
    assert missing_request.key == empty_request.key != filled_request.key  # what was sent is part of the key
    capped_request, capped = sent(max_tiles=3)
    assert capped.tiles == 0 and len(pictures_sent(capped_request)) == 2 and capped_request.key != filled_request.key


def test_a_document_of_the_set_is_never_a_reference(ingested) -> None:
    folder = ingested.root / "data" / "templates" / "permis_nouveau_recto"
    folder.mkdir(parents=True)
    shutil.copy(ingested.documents_path / "DOC-2.png", folder / "copie.png")
    fake = FakeGateway(GatewayAnswer("read", answer()), GatewayAnswer("read", answer()))
    run = run_inspect(ingested, inspect_settings(ingested.root), read_settings(ingested.root), gateway=fake)
    assert run.references_refused == 1 and [i.references for i in run.inspections] == [0, 0]


def test_pages_are_turned_upright_with_the_documents_own_reading(ingested) -> None:
    record = load_record(ingested, "DOC-1")
    reading = Reading(id="DOC-1", status="read", turns={1: 90}, fingerprint=record.fingerprint)
    assert page_turns(record, reading) == {1: 90}
    assert page_turns(record, reading.model_copy(update={"fingerprint": "another file"})) == {}
    assert page_turns(record, None) == {}
    fake = FakeGateway(GatewayAnswer("read", answer()))
    inspection, _, _ = inspect_document(record, ingested, inspect_settings(ingested.root, max_tiles=0),
                                        load_prompt(PROMPT), TYPES, fake, "terra-test", reading)
    url = pictures_sent(fake.requests[0])[0]["image_url"]["url"]
    assert Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size == (80, 120)  # 120 x 80, turned
    assert inspection.turns == {1: 90}


# --- gateway, cache, replay -------------------------------------------------------------------------------------


def test_the_gateway_keeps_inspections_and_never_builds_pictures_for_a_saved_answer(tmp_path) -> None:
    client = FakeClient(fake_response(parsed=answer()))
    log = TokenLog(tmp_path / "tokens.jsonl")
    live = AzureGateway(read_settings(tmp_path, max_completion_tokens=4000), AnswerCache(tmp_path / "answers"), log,
                        client=client, sleep=lambda _: None)

    def ask(build):
        return live.ask(ReadRequest(doc_id="DOC-1", key="i1", model="terra-test", prompt_version="v1", messages=build,
                                    schema=InspectionAnswer, stage="inspect"))

    first = ask(lambda: [{"role": "user", "content": "built"}])
    assert first.status == "read" and isinstance(first.answer, InspectionAnswer)
    assert client.calls[0]["response_format"] is InspectionAnswer and client.calls[0]["max_completion_tokens"] == 4000
    assert ask(lambda: pytest.fail("pictures built for a saved answer")).cached and len(client.calls) == 1
    assert json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])["stage"] == "inspect"


def test_inspections_are_saved_then_replayed_without_network(ingested) -> None:
    settings, connection = inspect_settings(ingested.root), read_settings(ingested.root)
    client = FakeClient(fake_response(parsed=answer()), fake_response(parsed=answer(readable="partly")))
    live = AzureGateway(connection, AnswerCache(ingested.cache_path / "read" / "answers"),
                        TokenLog(ingested.cache_path / "read" / "tokens.jsonl"), client=client, sleep=lambda _: None)
    first = run_inspect(ingested, settings, connection, gateway=live)
    assert len(first.inspections) == 2 and first.live_calls == 2 and load_inspection(ingested, "DOC-2").usable
    replayed = run_inspect(ingested, settings, connection, replay=True)
    assert len(replayed.inspections) == 2 and replayed.from_cache == 2 and replayed.live_calls == 0


def test_replay_without_saved_answers_saves_nothing(ingested) -> None:
    run = run_inspect(ingested, inspect_settings(ingested.root), read_settings(ingested.root), replay=True)
    assert run.inspections == [] and sorted(run.not_inspected) == ["DOC-1", "DOC-2"]
    assert load_inspection(ingested, "DOC-1") is None


def test_inspect_command_without_configuration_and_in_replay(ingested, monkeypatch, capsys) -> None:
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY", "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"INSPECT_DOC_TYPES_DIR": DOC_TYPES, "INSPECT_PROMPT_PATH": PROMPT, "INSPECT_ROOT": ingested.root,
                        "READ_ROOT": ingested.root, "INGEST_ROOT": ingested.root,
                        "INGEST_DOCUMENTS_DIR": "data/documents"}.items():
        monkeypatch.setenv(name, str(value))
    assert main(["inspect", "--limit", "1"]) == EXIT_SETUP_ERROR
    assert "not configured" in capsys.readouterr().err
    assert main(["inspect", "--replay"]) == EXIT_OK
    assert "not done    2 documents" in capsys.readouterr().out


# --- the four checks --------------------------------------------------------------------------------------------


def test_the_runner_hands_each_document_its_own_saved_inspection(ingested) -> None:
    record = load_record(ingested, "DOC-1")
    save_inspection(ingested, Inspection(id="DOC-1", status="inspected", answer=answer(), fingerprint=record.fingerprint))
    settings = CheckSettings(root=ingested.root, doc_types_dir=DOC_TYPES)
    assert document_context(ingested, settings, record).inspection.answer == answer()
    assert document_context(ingested, settings, load_record(ingested, "DOC-2")).inspection is None


def test_nothing_is_judged_on_an_unusable_inspection(ingested) -> None:
    hidden = judged(ingested, "DOC-1", answer(readable="no"))
    assert set(hidden) == INSPECTION_CHECKS and {result.outcome for result in hidden.values()} == {"cannot_tell"}
    refused = judged(ingested, "DOC-1", None, reason="refusal")
    assert all(result.outcome == "cannot_tell" and "refus du modèle" in result.evidence for result in refused.values())
    stale = judged(ingested, "DOC-1", answer(), fingerprint="0" * 64)
    assert {result.outcome for result in stale.values()} == {"cannot_tell"}


def test_a_missing_zone_fails_but_a_zone_of_the_other_face_does_not(ingested) -> None:
    photo, signature = "Photo du titulaire (rubrique 6)", "Signature du titulaire (rubrique 7)"
    missing = [ZoneObservation(name=photo, state="absent", page=1), ZoneObservation(name=signature, state="present", page=1)]
    result = judged(ingested, "DOC-1", answer(zones=missing))["base_form"]
    assert result.outcome == "fail" and f"« {photo} » absente (page 1)" in result.evidence and result.fields == [photo]
    elsewhere = [ZoneObservation(name=photo, state="not_applicable", page=0),
                 ZoneObservation(name=f"{signature} ; place : en bas", state="present", page=1)]
    assert judged(ingested, "DOC-1", answer(zones=elsewhere))["base_form"].outcome == "pass"
    unseen = [ZoneObservation(name=photo, state="unreadable", page=1)]
    assert judged(ingested, "DOC-1", answer(zones=unseen))["base_form"].outcome == "cannot_tell"


def test_a_zone_reported_twice_counts_once_and_a_cut_picture_proves_no_absence(ingested) -> None:
    photo, signature = "Photo du titulaire (rubrique 6)", "Signature du titulaire (rubrique 7)"
    # one report per page of a two-sided file: present on one page outweighs absent on the other, whatever the order
    for states in (("absent", "present"), ("present", "absent")):
        twice = [ZoneObservation(name=photo, state=states[0], page=2), ZoneObservation(name=photo, state=states[1], page=1)]
        assert judged(ingested, "DOC-1", answer(zones=twice))["base_form"].outcome == "pass"
    # the signature has no stated place on the card: "displaced" cannot be judged for it
    moved = [ZoneObservation(name=photo, state="present", page=1), ZoneObservation(name=signature, state="displaced", page=1)]
    assert judged(ingested, "DOC-1", answer(zones=moved))["base_form"].outcome == "pass"
    # part of the document is out of the picture: an "absent" zone may simply be outside it
    cut = [ZoneObservation(name=photo, state="absent", page=1), ZoneObservation(name=signature, state="present", page=1)]
    assert judged(ingested, "DOC-1", answer(zones=cut, readable="partly"))["base_form"].outcome == "pass"
    # a page number that is not a page of the document (digits seen on it) never reaches the evidence
    odd = [ZoneObservation(name=photo, state="absent", page=2024)]
    result = judged(ingested, "DOC-1", answer(zones=odd))["base_form"]
    assert result.outcome == "fail" and "2024" not in result.evidence


def test_an_answer_that_describes_nothing_proves_nothing(ingested) -> None:
    results = judged(ingested, "DOC-1", answer())
    assert {result.outcome for result in results.values()} == {"cannot_tell"}


@pytest.mark.parametrize(
    ("said", "field"),
    [("rubrique 2", "2."), ("2. Prénoms", "2."), ("1.", "1."), ("4a", "4a."),
     ("4c (page 1)", None), ("4b. (ligne 3)", None), ("page 1, en-tête", None), ("titre en haut de la page 1", None)],
)
def test_a_page_or_a_line_number_never_names_a_field(said, field) -> None:
    from fraudcheck.checks.inspection._inspection import match

    assert match(said, ["1.", "2.", "3.", "4a.", "5.", "9."]) == field


@pytest.mark.parametrize(
    ("printed", "expected", "counts"),
    [("échéance annuel", "échéance annuelle", True), ("et de", "est de", True),
     ("date avec un séparateur en trop", "date sans séparateur en trop", True),
     ("DRIVING LICENCE", "PERMIS DE CONDUIRE", False),  # another wording, not a mistake
     ("4a. Date de délivrance", "4a.", False),  # a label given with its caption
     ("Nom", "", False)],  # nothing to compare with
)
def test_only_two_texts_that_look_alike_make_a_mistake(ingested, printed, expected, counts) -> None:
    error = FixedTextError(page=1, zone="titre", printed=printed, expected=expected)
    zone = ZoneObservation(name="Photo du titulaire (rubrique 6)", state="present", page=1)
    result = judged(ingested, "DOC-1", answer(fixed_text_errors=[error], zones=[zone]))["fixed_text_spelling"]
    assert result.outcome == ("fail" if counts else "pass")


def test_a_misspelled_form_text_fails_but_accents_and_case_do_not(ingested) -> None:
    accents = FixedTextError(page=1, zone="titre", printed="PERMIS DE CONDUIRE", expected="Permis de conduire")
    assert judged(ingested, "DOC-1", answer(fixed_text_errors=[accents]))["fixed_text_spelling"].outcome == "pass"
    typo = FixedTextError(page=1, zone=INVENTED, printed="PERMIS DE CONDIURE", expected="PERMIS DE CONDUIRE")
    result = judged(ingested, "DOC-1", answer(fixed_text_errors=[typo]))["fixed_text_spelling"]
    assert result.outcome == "fail" and "page 1 (« PERMIS DE CONDUIRE »)" in result.evidence
    assert INVENTED not in result.evidence and "CONDIURE" not in result.evidence  # the model's words stay in details
    assert result.details["errors"][0]["printed"] == "PERMIS DE CONDIURE"


def test_fields_out_of_order_fail_only_when_the_form_names_them(ingested) -> None:
    swapped = FieldOrder(verdict="out_of_order", swapped=["rubrique 2", "1."])  # first names printed before the surname
    result = judged(ingested, "DOC-1", answer(field_order=swapped))["field_order"]
    assert result.outcome == "fail" and result.fields == ["1.", "2."]
    unknown = FieldOrder(verdict="out_of_order", swapped=[INVENTED])
    assert judged(ingested, "DOC-1", answer(field_order=unknown))["field_order"].outcome == "cannot_tell"
    # 4b and 4c are not in the card's order list (4c sits beside 4a on the real card): naming them proves nothing
    beside = FieldOrder(verdict="out_of_order", swapped=["4b.", "4c."])
    assert judged(ingested, "DOC-1", answer(field_order=beside))["field_order"].outcome == "cannot_tell"
    in_order = FieldOrder(verdict="in_order", swapped=[])
    assert judged(ingested, "DOC-1", answer(field_order=in_order))["field_order"].outcome == "pass"


def test_a_type_without_zones_or_order_skips_those_checks(ingested) -> None:
    assert set(judged(ingested, "DOC-1", answer(), doc_type="permis_etranger")) == {"fixed_text_spelling",
                                                                                     "typography_picture"}


def test_only_clear_lettering_differences_fail(ingested) -> None:
    slight = TypographyObservation(page=1, zone="5.", aspects=["size"], strength="slight", description="un peu plus petit")
    assert judged(ingested, "DOC-1", answer(typography=[slight]))["typography_picture"].outcome == "pass"
    clear = TypographyObservation(page=1, zone="5.", aspects=["font", "background_patch"], strength="clear",
                                  description=f"police différente autour de {INVENTED}")
    result = judged(ingested, "DOC-1", answer(typography=[slight, clear]))["typography_picture"]
    assert result.outcome == "fail" and "page 1 (« 5. ») : police, fond rapporté" in result.evidence
    assert INVENTED not in result.evidence


def test_lettering_fails_on_font_sharpness_or_a_patch_only(ingested) -> None:
    """Weight, size, colour or spacing alone are found on genuine forms too: reported, never a failure."""
    def seen(*aspects: str) -> InspectionAnswer:
        return answer(typography=[TypographyObservation(page=1, zone="5.", aspects=list(aspects), strength="clear",
                                                        description="différent des zones voisines")])

    bold = judged(ingested, "DOC-1", seen("weight", "size"))["typography_picture"]
    assert bold.outcome == "pass" and "1 différence(s) de graisse" in bold.evidence
    for aspect in ("font", "sharpness", "background_patch"):
        assert judged(ingested, "DOC-1", seen(aspect, "weight"))["typography_picture"].outcome == "fail"


def test_lettering_is_not_judged_on_a_layered_scan_nor_on_a_pdf_with_real_text(ingested) -> None:
    sharper = answer(typography=[TypographyObservation(page=1, zone="5.", aspects=["sharpness"], strength="clear",
                                                       description="plus net que les zones voisines")])
    record = load_record(ingested, "DOC-1")
    # an office scanner saves a background picture plus one-bit text masks: sharpness differs without any retouching
    layered = record.model_copy(update={"pages": [page.model_copy(update={"scanner_layers": True}) for page in record.pages]})
    result = judged(ingested, "DOC-1", sharper, record=layered)["typography_picture"]
    assert result.outcome == "cannot_tell" and "couches" in result.evidence
    # a PDF with real text is judged on its own fonts by the laptop checks, not on what a model sees
    typed = record.model_copy(update={"kind": "pdf_text"})
    assert "typography_picture" not in judged(ingested, "DOC-1", sharper, record=typed)


def test_a_displaced_zone_fails_only_where_the_layout_is_fixed(ingested) -> None:
    moved = [ZoneObservation(name="Photo du titulaire (rubrique 6)", state="displaced", page=1)]
    assert judged(ingested, "DOC-1", answer(zones=moved))["base_form"].outcome == "fail"  # the licence card: fixed
    holder = [ZoneObservation(name="Titulaire du compte", state="displaced", page=1)]
    result = judged(ingested, "DOC-2", answer(zones=holder))["base_form"]  # a RIB: each bank has its own layout
    assert result.outcome == "pass" and "seule la présence" in result.evidence


def test_fine_print_and_a_synthetic_look_are_kept_as_information(ingested) -> None:
    seen = answer(fine_print=[FinePrint(page=1, zone="bas de page", state="garbled")], capture="synthetic_image")
    results = judged(ingested, "DOC-1", seen)
    assert results["typography_picture"].outcome == "pass"
    assert results["typography_picture"].details["fine_print"][0]["state"] == "garbled"
    assert results["base_form"].details["capture"] == "synthetic_image"


def test_the_prompt_says_whether_the_layout_is_fixed() -> None:
    prompt = load_prompt(PROMPT)
    fixed = user_text(prompt, None, doc_type_named("carte_grise", TYPES), pages=1, tiles=0, references=0)
    varies = user_text(prompt, None, doc_type_named("rib", TYPES), pages=1, tiles=0, references=0)
    assert "fixée par la réglementation" in fixed and "seulement leur présence" in varies
    assert "synthetic_image" in prompt.system and "fine_print" in prompt.system
