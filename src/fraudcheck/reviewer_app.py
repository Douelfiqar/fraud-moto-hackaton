"""The reviewer screen: a claims handler sees each decision with its reasons, and has the last word.

Run it with ``streamlit run src/fraudcheck/reviewer_app.py`` after ``python -m fraudcheck run``.

For each document the screen shows the page pictures, the automatic decision with its
justification, clues and confidence, and every check's answer. The person then records
their own decision (fake or genuine, with a reason), which replaces the automatic one:
it is written to the review file, the decision is computed again and the answer file is
rewritten. Nothing is sent anywhere: the screen reads the local cache only.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import fraudcheck  # noqa: F401
except ImportError:  # started with "streamlit run" from a checkout that is not installed
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from fraudcheck.checks.runner import load_results
from fraudcheck.decision.answer import AnswerFileError, write_answer
from fraudcheck.decision.decide import (
    Decision,
    DecisionSetupError,
    ReviewDecision,
    decide,
    load_decision_config,
    load_decisions,
    load_review_decisions,
)
from fraudcheck.decision.review import remove_review_decision, save_review_decision
from fraudcheck.decision.settings import load_decision_settings
from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.discover import natural_key
from fraudcheck.ingest.pipeline import load_record
from fraudcheck.ingest.settings import load_settings
from fraudcheck.ingest.storage import CacheWriteError

FLAGGED, TO_REVIEW, EVERYTHING = "Signalés comme faux", "À relire (un contrôle sans confiance a échoué)", "Tous les documents"
FAKE, GENUINE, AUTOMATIC = "Faux", "Authentique", "Aucune : garder la décision automatique"
OUTCOMES = {"fail": "échec", "cannot_tell": "ne sait pas", "pass": "conforme"}
FAMILIES = {"licence": "permis", "releve": "relevé d'information", "identity": "pièce d'identité ou titre de séjour",
            "carte_grise": "carte grise", "rib": "RIB"}


def recompute(ingest, settings) -> str | None:
    """Decide again and rewrite the answer file; the error to show when that cannot be done."""
    try:
        run = decide(ingest, settings)
        write_answer(ingest, settings, run.decisions, run.models)
    except (DecisionSetupError, ConfigError, AnswerFileError, CacheWriteError) as exc:
        return str(exc)
    return None


def label(decision: Decision, family: str | None) -> str:
    state = "signalé" if decision.flagged else "non signalé"
    if decision.flagged and decision.confidence is not None:
        state += f" ({decision.confidence:.2f})".replace(".", ",")
    person = " · décidé par une personne" if decision.reviewed else ""
    return f"{decision.id} · {FAMILIES.get(family or '', 'famille inconnue')} · {state}{person}"


def order(decisions: list[Decision]) -> list[Decision]:
    """Flagged documents first, the least sure at the top, then the others by id."""
    return sorted(decisions, key=lambda d: (not d.flagged, d.confidence if d.confidence is not None else 1.0, natural_key(d.id)))


def show_decision(decision: Decision) -> None:
    if decision.flagged:
        confidence = "" if decision.confidence is None else f" · confiance {decision.confidence:.2f}".replace(".", ",")
        kind = f" · {decision.fraud_type}" if decision.fraud_type else ""
        st.error(f"Signalé comme faux{confidence}{kind}")
        st.write(decision.justification or "")
        if decision.clues:
            st.markdown("**Indices**\n" + "\n".join(f"- {clue}" for clue in decision.clues))
    elif decision.line == "unreadable":
        st.warning("Document illisible : non signalé, à regarder par une personne.")
    else:
        st.success("Non signalé.")
    if decision.reviewed:
        st.info("Cette décision est celle d'une personne ; elle a remplacé la décision automatique.")


def show_checks(decision: Decision, results: dict, trusted: set[str]) -> None:
    rows = []
    for record_id in decision.documents or [decision.id]:
        for name, result in results.get(record_id, {}).items():
            rows.append((result.outcome != "fail", name not in trusted, result.outcome == "pass", name, result))
    if not rows:
        st.caption("Aucun contrôle n'a été lancé sur ce document.")
        return
    failed = [row for row in rows if row[4].outcome == "fail"]
    st.markdown(f"**Contrôles** : {len(rows)} lancés, {len(failed)} en échec")
    for _, _, _, name, result in sorted(row for row in rows if row[4].outcome != "pass"):
        trust = "de confiance" if name in trusted else "informatif"
        st.markdown(f"- `{name}` ({trust}) · **{OUTCOMES[result.outcome]}** · {result.evidence}")
    with st.expander(f"Contrôles conformes ({sum(1 for row in rows if row[4].outcome == 'pass')})"):
        for _, _, _, name, result in sorted(row for row in rows if row[4].outcome == "pass"):
            st.markdown(f"- `{name}` · {result.evidence}")


def main() -> None:
    st.set_page_config(page_title="Contrôle des justificatifs", layout="wide")
    ingest, settings = load_settings(), load_decision_settings()
    decisions = load_decisions(ingest)
    if not decisions:
        st.title("Contrôle des justificatifs")
        st.warning("Aucune décision enregistrée. Lancez d'abord : python -m fraudcheck run")
        return
    try:
        trusted = set(load_decision_config(settings.decision_file).trusted)
        reviews = load_review_decisions(settings.review_decisions_file)
    except ConfigError as exc:
        st.error(f"Réglages illisibles : {exc}")
        return
    results = load_results(ingest)
    records = {decision.id: load_record(ingest, (decision.documents or [decision.id])[0]) for decision in decisions}

    st.title("Contrôle des justificatifs")
    st.caption("L'outil signale et explique ; une personne valide. Sa décision remplace la décision automatique.")
    counts = st.columns(4)
    counts[0].metric("Documents", len(decisions))
    counts[1].metric("Signalés comme faux", sum(decision.flagged for decision in decisions))
    counts[2].metric("Décidés par une personne", sum(decision.reviewed for decision in decisions))
    counts[3].metric("À relire", sum(decision.review and not decision.flagged for decision in decisions))
    if message := st.session_state.pop("saved", None):
        st.success(message)

    with st.sidebar:
        st.header("Documents")
        scope = st.radio("Afficher", [FLAGGED, TO_REVIEW, EVERYTHING], key="scope")
        families = sorted({record.family for record in records.values() if record and record.family})
        chosen = st.multiselect("Familles", families, default=families, format_func=lambda name: FAMILIES.get(name, name),
                                key="families")
        listed = [decision for decision in order(decisions)
                  if (scope == EVERYTHING or (scope == FLAGGED and decision.flagged)
                      or (scope == TO_REVIEW and decision.review and not decision.flagged))
                  and (not records[decision.id] or records[decision.id].family in chosen or not records[decision.id].family)]
        if not listed:
            st.info("Aucun document dans cette liste.")
            return
        by_id = {decision.id: decision for decision in listed}
        doc_id = st.selectbox("Document", list(by_id), key="document",
                              format_func=lambda name: label(by_id[name], records[name].family if records[name] else None))
    decision, record = by_id[doc_id], records[doc_id]

    pictures, panel = st.columns([3, 2])
    with pictures:
        st.subheader(f"{decision.id} · {record.nature if record and record.nature else 'nature inconnue'}")
        for page in record.pages if record else []:
            path = ingest.resolve(page.picture)
            if path.is_file():
                st.image(str(path), caption=f"Page {page.number}")
        if not record or not record.pages:
            st.warning("Aucune page lisible pour ce document.")
    with panel:
        st.subheader("Décision de l'outil")
        show_decision(decision)
        show_checks(decision, results, trusted)

        st.subheader("Votre décision")
        current = reviews.get(doc_id)
        choices = [FAKE, GENUINE, AUTOMATIC]
        start = 2 if current is None else (0 if current.fake else 1)
        verdict = st.radio("Après examen du document", choices, index=start, key=f"verdict-{doc_id}")
        reason = st.text_area("Motif (ne recopiez aucun nom, numéro ou date du document)",
                              value=current.reason if current else "", key=f"reason-{doc_id}")
        generated = st.checkbox("Document généré ou redessiné par IA", value=bool(current and current.generated),
                                key=f"generated-{doc_id}")
        if st.button("Enregistrer ma décision", key=f"save-{doc_id}", type="primary"):
            if verdict == FAKE and not reason.strip():
                st.error("Un document jugé faux demande un motif.")
                return
            path = settings.review_decisions_file
            if verdict == AUTOMATIC:
                remove_review_decision(path, doc_id)
                saved = f"{doc_id} : décision automatique rétablie."
            else:
                save_review_decision(path, ReviewDecision(id=doc_id, fake=verdict == FAKE, reason=reason.strip(),
                                                          generated=generated and verdict == FAKE))
                saved = f"{doc_id} : décision enregistrée ({verdict.lower()})."
            if error := recompute(ingest, settings):
                st.error(f"Décision enregistrée, mais le fichier de réponse n'a pas pu être réécrit : {error}")
                return
            st.session_state["saved"] = saved + " Le fichier de réponse a été réécrit."
            st.rerun()


main()
