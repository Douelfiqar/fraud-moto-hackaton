"""Command line: ``python -m fraudcheck ingest|check|decide|evaluate|read|inspect|run``.

Prints counts only (kinds, families, pages, warnings, errors), never document content.
Exit status: 0 when every document was processed, 1 when some documents carry an
error, 2 when the run could not start (missing folder, invalid setting or config).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.models import FAMILIES
from fraudcheck.ingest.pipeline import IngestSetupError, RunResult, Summary, run_ingest, summarise
from fraudcheck.ingest.settings import IngestSettings, load_settings
from fraudcheck.ingest.storage import CacheWriteError

EXIT_OK, EXIT_DOCUMENT_ERRORS, EXIT_SETUP_ERROR, EXIT_INTERRUPTED = 0, 1, 2, 130
_KIND_ORDER = ("picture", "pdf_scan", "pdf_text", "unknown")
_SOURCE_LABELS = {
    "original": "used as received",
    "working_copy": "working copies",
    "rendered": "drawn from PDFs",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fraudcheck",
        description="Fraud detection for motorbike-insurance proof documents.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    ingest = commands.add_parser(
        "ingest",
        help="step 1: turn every document into a record with page pictures",
        description="Turn every document into a record with page pictures, under the cache folder. "
        "Documents whose file and settings are unchanged are taken from the cache.",
    )
    ingest.add_argument(
        "targets",
        nargs="*",
        metavar="DOC",
        help="document ids (DOC-042) or file paths; default: the whole documents folder",
    )
    ingest.add_argument("--force", action="store_true", help="process again even when the cache is up to date")
    ingest.add_argument("--documents", type=Path, metavar="DIR", help="documents folder (setting documents_dir)")
    ingest.add_argument("--catalogue", type=Path, metavar="FILE", help="organisers' spreadsheet (catalogue_path)")
    ingest.add_argument("--cache", type=Path, metavar="DIR", help="output folder (setting cache_dir)")
    ingest.add_argument("--dpi", type=int, metavar="N", help="pixels per inch for PDF pages (setting pdf_dpi)")
    ingest.add_argument(
        "-v", "--verbose", action="store_true", help="list the documents with warnings or errors (ids and codes)"
    )
    check = commands.add_parser(
        "check",
        help="step 4: run every check and write the check table",
        description="Run every registered check on every ingested document and write cache/checks/table.csv.",
    )
    check.add_argument("--only", nargs="+", metavar="CHECK", help="run only these checks (others keep their results)")
    check.add_argument("--cache", type=Path, metavar="DIR", help="cache folder (setting INGEST_CACHE_DIR)")
    check.add_argument("-v", "--verbose", action="store_true", help="more messages")
    decide = commands.add_parser(
        "decide",
        help="step 5: decide each document and write the answer file",
        description="Flag documents whose trusted checks failed (config/decision.toml), write the review list "
        "and the answer file in the organisers' TOML format, checked by reading it back.",
    )
    _answer_options(decide)
    evaluate = commands.add_parser(
        "evaluate",
        help="step 6: score the decisions on marked examples",
        description="Count fakes caught, wrong flags, the false-positive rate, the score and each check's hits.",
    )
    _evaluation_options(evaluate)
    read = commands.add_parser(
        "read",
        help="step 2: read each document with Azure (one request per document)",
        description="Send each document's pages to the team's Azure deployment and save what it reads. "
        "Answers are kept on disk, so a document is never paid for twice. Try --limit 3 before the batch.",
    )
    _read_options(read)
    inspect = commands.add_parser(
        "inspect",
        help="step 3: inspect each document's pictures with Azure (one more request per document)",
        description="Send each document's upright pages, zoom tiles and reference pictures (data/templates/<type>/) to "
        "the team's Azure deployment with the base form of its type (config/doc_types), and save what the model "
        "observes. Separate from the reading; answers are kept on disk. Try --limit 3 before the batch.",
    )
    _read_options(inspect, verb="inspect")
    run = commands.add_parser(
        "run",
        help="everything in order: ingest, (read), (inspect), check, decide, evaluate",
        description="Run the whole pipeline. Without --read and --inspect, the readings and inspections already saved "
        "are used and Azure is not called; with them, documents not yet done are sent to Azure (or taken from saved "
        "answers with --replay).",
    )
    run.add_argument("--read", action="store_true", help="read documents with Azure before the checks")
    run.add_argument("--inspect", action="store_true", help="inspect documents with Azure before the checks")
    run.add_argument("--reinspect", action="store_true", help="inspect again even when an answer is saved (costs)")
    _read_options(run, prefix="read-")
    _answer_options(run)
    run.add_argument("--marked", type=Path, metavar="FILE", help="marked examples CSV (setting DECIDE_MARKED_EXAMPLES_PATH)")
    run.add_argument("--evaluated", type=int, metavar="N", help="documents in the scored part (setting DECIDE_EVALUATED_DOCUMENTS)")
    return parser


def _read_options(parser: argparse.ArgumentParser, prefix: str = "", verb: str = "read") -> None:
    what = "read and inspect" if prefix else verb
    parser.add_argument(f"--{prefix}only", nargs="+", metavar="ID", dest="only", help=f"{what} only these documents")
    parser.add_argument(f"--{prefix}limit", type=int, metavar="N", dest="limit", help=f"{what} only the first N documents")
    parser.add_argument("--replay", action="store_true", help="answer from saved answers only: no network, no cost")
    parser.add_argument("--strong", action="store_true", help="use the stronger deployment (AZURE_OPENAI_MODEL_SOL)")
    parser.add_argument(f"--re{verb}", action="store_true", help="ask Azure again even when an answer is saved (costs)")
    if not prefix:
        parser.add_argument("--cache", type=Path, metavar="DIR", help="cache folder (setting INGEST_CACHE_DIR)")
        parser.add_argument("-v", "--verbose", action="store_true", help="more messages")


def _answer_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--team", metavar="NAME", help="exact team name (setting DECIDE_TEAM)")
    parser.add_argument("--stage", choices=["intermediaire", "final"], help="deposit stage (setting DECIDE_STAGE)")
    parser.add_argument("--commit", metavar="SHA", help="commit written in the file (default: the current one)")
    parser.add_argument("--cache", type=Path, metavar="DIR", help="cache folder (setting INGEST_CACHE_DIR)")
    parser.add_argument("-v", "--verbose", action="store_true", help="list flagged documents with their checks")


def _evaluation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--marked", type=Path, metavar="FILE", help="marked examples CSV (setting DECIDE_MARKED_EXAMPLES_PATH)")
    parser.add_argument("--evaluated", type=int, metavar="N", help="documents in the scored part (setting DECIDE_EVALUATED_DOCUMENTS)")
    parser.add_argument("--cache", type=Path, metavar="DIR", help="cache folder (setting INGEST_CACHE_DIR)")
    parser.add_argument("-v", "--verbose", action="store_true", help="more messages")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # an odd character in a path must not crash the summary
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")
    handlers = {"ingest": _ingest, "check": _check, "decide": _decide, "evaluate": _evaluate, "read": _read,
                "inspect": _inspect, "run": _run}
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("\nInterrupted. Files written so far are kept and will be reused.", file=sys.stderr)
        return EXIT_INTERRUPTED


def _ingest_settings(args: argparse.Namespace) -> IngestSettings:
    cache = getattr(args, "cache", None)
    return load_settings({"cache_dir": Path.cwd() / cache if cache else None})


def _check(args: argparse.Namespace) -> int:
    from fraudcheck.checks.runner import ChecksSetupError, run_checks
    from fraudcheck.checks.settings import load_check_settings

    try:
        ingest, settings = _ingest_settings(args), load_check_settings()
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        run = run_checks(ingest, settings, only=args.only or ())
    except (ChecksSetupError, CacheWriteError) as exc:
        print(f"Cannot run the checks: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    print(format_checks(run, ingest))
    return EXIT_OK


def format_checks(run, ingest: IngestSettings) -> str:
    """Per check: how many documents passed, failed or could not be told, and the ids that failed."""
    lines = [f"Checks: {len(run.results)} documents, {len(run.checks)} checks in {run.seconds:.1f} s"]
    for name in run.checks:
        answers = [answers[name] for answers in run.results.values() if name in answers]
        counts = Counter(answer.outcome or "score" for answer in answers)
        failed = sorted(doc_id for doc_id, answers in run.results.items() if name in answers and answers[name].outcome == "fail")
        tally = ", ".join(f"{counts[key]} {key}" for key in ("pass", "fail", "cannot_tell", "score") if counts.get(key))
        lines.append(f"  {name:<20} {len(answers)} documents: {tally or 'none'}" + (f" ({', '.join(failed)})" if failed else ""))
    lines.append(f"  table                {ingest.display(run.table_path)}")
    return "\n".join(lines)


def _decision_settings(args: argparse.Namespace):
    from fraudcheck.decision.settings import load_decision_settings

    marked = getattr(args, "marked", None)
    return load_decision_settings(
        {
            "team": getattr(args, "team", None),
            "stage": getattr(args, "stage", None),
            "commit": getattr(args, "commit", None),
            "marked_examples_path": Path.cwd() / marked if marked else None,
            "evaluated_documents": getattr(args, "evaluated", None),
        }
    )


def _decide(args: argparse.Namespace) -> int:
    from fraudcheck.decision.answer import AnswerFileError, write_answer
    from fraudcheck.decision.decide import DecisionSetupError, decide

    try:
        ingest, settings = _ingest_settings(args), _decision_settings(args)
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        run = decide(ingest, settings)
        answer = write_answer(ingest, settings, run.decisions, run.models)
    except (DecisionSetupError, ConfigError, AnswerFileError, CacheWriteError) as exc:
        print(f"Cannot decide: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    print(format_decisions(run, answer, ingest, args.verbose))
    return EXIT_OK


def format_decisions(run, answer, ingest: IngestSettings, verbose: bool = False) -> str:
    lines_by = Counter(decision.line for decision in run.decisions)
    flagged = [decision for decision in run.decisions if decision.flagged]
    lines = [
        f"Decision: {len(run.decisions)} documents, {len(flagged)} flagged"
        + (f" ({', '.join(decision.id for decision in flagged)})" if flagged else ""),
        f"  lines      trusted check failed {lines_by['trusted_check_failed']} | unreadable {lines_by['unreadable']}"
        f" | not flagged {lines_by['not_flagged']} | decided by a person {lines_by['reviewed']}",
        f"  review     {sum(decision.review for decision in run.decisions)} documents to look at: {ingest.display(run.review_path)}",
        f"  answer     {ingest.display(answer.path)} (team, stage and {answer.documents} documents checked by reading it back)",
        f"  commit     {answer.commit or 'unknown (no Git)'}",
    ]
    if answer.dirty:
        lines.append("  note       files differ from that commit: commit before depositing, so the SHA describes the code")
    if verbose:
        lines += [f"  {decision.id:<10} {', '.join(decision.trusted_failed)} (confiance {decision.confidence})" for decision in flagged]
    return "\n".join(lines)


def _evaluate(args: argparse.Namespace) -> int:
    from fraudcheck.checks.runner import load_results
    from fraudcheck.decision.decide import load_decisions
    from fraudcheck.decision.evaluate import evaluate, format_evaluation, load_marked

    try:
        ingest, settings = _ingest_settings(args), _decision_settings(args)
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    decisions = load_decisions(ingest)
    if not decisions:
        print("Cannot evaluate: no decisions in the cache; run 'python -m fraudcheck decide' first", file=sys.stderr)
        return EXIT_SETUP_ERROR
    report = evaluate(decisions, load_results(ingest), load_marked(settings.marked_examples_file), settings.evaluated_documents)
    print(format_evaluation(report))
    return EXIT_OK


def _read(args: argparse.Namespace) -> int:
    from fraudcheck.read.gateway import GatewaySetupError
    from fraudcheck.read.reader import ReadSetupError, run_read
    from fraudcheck.read.settings import load_read_settings

    try:
        ingest = _ingest_settings(args)
        settings = load_read_settings({"use_strong": True if args.strong else None})
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        run = run_read(ingest, settings, only=args.only or (), limit=args.limit, replay=args.replay,
                       force=args.reread, progress=_progress_printer())
    except (ReadSetupError, GatewaySetupError, ConfigError, CacheWriteError) as exc:
        _clear_progress()
        print(f"Cannot read: {exc}", file=sys.stderr)
        if isinstance(exc, GatewaySetupError):
            print("Copy .env.example to .env and fill it, or use --replay to read from saved answers.", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _clear_progress()
    print(format_read(run, settings, ingest))
    return EXIT_OK


def format_read(run, settings, ingest: IngestSettings) -> str:
    from fraudcheck.read.gateway import TOKEN_LOG

    outcomes = Counter(reading.status for reading in run.readings)
    reasons = Counter(reading.reason for reading in run.readings if reading.status == "no_reading")
    cost = run.cost(settings)
    lines = [
        f"Read: {len(run.readings) + len(run.not_read)} documents in {run.seconds:.1f} s with {run.model}"
        + (" (replay: saved answers only)" if run.replay else ""),
        f"  readings   {outcomes['read']} read, {outcomes['no_reading']} no reading"
        + (f" ({', '.join(f'{reason} {count}' for reason, count in reasons.items())})" if reasons else ""),
        f"  calls      {run.live_calls} to Azure, {run.from_cache} answers from the disk cache",
        f"  tokens     {run.input_tokens} in, {run.output_tokens} out"
        + (f", about {cost:.2f} EUR" if cost is not None else " (set READ_PRICE_INPUT_PER_MILLION and _OUTPUT_ to see a cost)"),
        f"  token log  {ingest.display(ingest.cache_path / TOKEN_LOG)}",
    ]
    if run.not_read:
        lines.append(f"  not read   {len(run.not_read)} documents (no saved answer or no network): nothing saved for them")
    return "\n".join(lines)


def _inspect(args: argparse.Namespace) -> int:
    from fraudcheck.inspect.inspector import run_inspect
    from fraudcheck.inspect.settings import load_inspect_settings
    from fraudcheck.read.gateway import GatewaySetupError
    from fraudcheck.read.reader import ReadSetupError
    from fraudcheck.read.settings import load_read_settings

    try:
        ingest = _ingest_settings(args)
        connection = load_read_settings({"use_strong": True if args.strong else None})
        settings = load_inspect_settings()
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        run = run_inspect(ingest, settings, connection, only=args.only or (), limit=args.limit, replay=args.replay,
                          force=args.reinspect, progress=_progress_printer())
    except (ReadSetupError, GatewaySetupError, ConfigError, CacheWriteError) as exc:
        _clear_progress()
        print(f"Cannot inspect: {exc}", file=sys.stderr)
        if isinstance(exc, GatewaySetupError):
            print("Copy .env.example to .env and fill it, or use --replay to inspect from saved answers.", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _clear_progress()
    print(format_inspect(run, connection, ingest))
    return EXIT_OK


def format_inspect(run, connection, ingest: IngestSettings) -> str:
    from fraudcheck.read.gateway import TOKEN_LOG

    outcomes = Counter(inspection.status for inspection in run.inspections)
    reasons = Counter(inspection.reason for inspection in run.inspections if inspection.status == "no_inspection")
    hidden = sum(1 for inspection in run.inspections if inspection.answer is not None and inspection.answer.readable == "no")
    referenced = sum(1 for inspection in run.inspections if inspection.references)
    cost = run.cost(connection)
    lines = [
        f"Inspect: {len(run.inspections) + len(run.not_inspected)} documents in {run.seconds:.1f} s with {run.model}"
        + (" (replay: saved answers only)" if run.replay else ""),
        f"  inspections {outcomes['inspected']} inspected ({hidden} not visible enough), "
        f"{outcomes['no_inspection']} not inspected"
        + (f" ({', '.join(f'{reason} {count}' for reason, count in reasons.items())})" if reasons else ""),
        f"  pictures    {sum(i.tiles for i in run.inspections)} zoom tiles; reference pictures for {referenced} documents"
        + (f"; {run.references_refused} refused (identical to a document of the set)" if run.references_refused else ""),
        f"  calls       {run.live_calls} to Azure, {run.from_cache} answers from the disk cache",
        f"  tokens      {run.input_tokens} in, {run.output_tokens} out"
        + (f", about {cost:.2f} EUR" if cost is not None else " (set READ_PRICE_INPUT_PER_MILLION and _OUTPUT_ to see a cost)"),
        f"  token log   {ingest.display(ingest.cache_path / TOKEN_LOG)}",
    ]
    if run.not_inspected:
        lines.append(f"  not done    {len(run.not_inspected)} documents (no saved answer or no network): nothing saved for them")
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> int:
    """The whole pipeline, stage after stage; each stage prints its own summary."""
    from fraudcheck.checks.runner import ChecksSetupError, run_checks
    from fraudcheck.checks.settings import load_check_settings
    from fraudcheck.decision.answer import AnswerFileError, write_answer
    from fraudcheck.decision.decide import DecisionSetupError, decide
    from fraudcheck.decision.evaluate import evaluate, format_evaluation, load_marked
    from fraudcheck.inspect.inspector import run_inspect
    from fraudcheck.inspect.settings import load_inspect_settings
    from fraudcheck.read.gateway import GatewaySetupError
    from fraudcheck.read.reader import ReadSetupError, run_read
    from fraudcheck.read.settings import load_read_settings

    try:
        ingest = _ingest_settings(args)
        checks, decision = load_check_settings(), _decision_settings(args)
        reading = load_read_settings({"use_strong": True if args.strong else None})
        inspection = load_inspect_settings()
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        ingested = run_ingest(ingest, progress=_progress_printer())
        _clear_progress()
        print(format_summary(summarise(ingested), ingest), end="\n\n")
        if args.read or args.replay:
            try:
                read = run_read(ingest, reading, only=args.only or (), limit=args.limit, replay=args.replay,
                                force=args.reread, progress=_progress_printer())
                _clear_progress()
                print(format_read(read, reading, ingest), end="\n\n")
            except GatewaySetupError as exc:
                _clear_progress()
                print(f"Reading skipped: {exc}\n", file=sys.stderr)
        else:
            print("Reading skipped: no --read; the checks use the readings already saved.\n")
        if args.inspect:
            try:
                inspected = run_inspect(ingest, inspection, reading, only=args.only or (), limit=args.limit,
                                        replay=args.replay, force=args.reinspect, progress=_progress_printer())
                _clear_progress()
                print(format_inspect(inspected, reading, ingest), end="\n\n")
            except GatewaySetupError as exc:
                _clear_progress()
                print(f"Inspection skipped: {exc}\n", file=sys.stderr)
        else:
            print("Inspection skipped: no --inspect; the checks use the inspections already saved.\n")
        checked = run_checks(ingest, checks)
        print(format_checks(checked, ingest), end="\n\n")
        decided = decide(ingest, decision, checked.results)
        answer = write_answer(ingest, decision, decided.decisions, decided.models)
        print(format_decisions(decided, answer, ingest, args.verbose), end="\n\n")
        marked = load_marked(decision.marked_examples_file)
        print(format_evaluation(evaluate(decided.decisions, checked.results, marked, decision.evaluated_documents)))
    except (IngestSetupError, ConfigError, CacheWriteError, ChecksSetupError, ReadSetupError, DecisionSetupError,
            AnswerFileError) as exc:
        _clear_progress()
        print(f"Cannot run the pipeline: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    return EXIT_OK


def _ingest(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    overrides = {
        "documents_dir": cwd / args.documents if args.documents else None,
        "catalogue_path": cwd / args.catalogue if args.catalogue else None,
        "cache_dir": cwd / args.cache if args.cache else None,
        "pdf_dpi": args.dpi,
    }
    try:
        settings = load_settings(overrides)
    except ValidationError as exc:
        print(f"Invalid setting:\n{exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        result = run_ingest(settings, args.targets, force=args.force, progress=_progress_printer())
    except (IngestSetupError, ConfigError, CacheWriteError) as exc:
        _clear_progress()
        print(f"Cannot run Ingest: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _clear_progress()
    summary = summarise(result)
    print(format_summary(summary, settings))
    if args.verbose:
        print(format_problems(result))
    return EXIT_DOCUMENT_ERRORS if summary.errors else EXIT_OK


def _progress_printer():
    if not sys.stderr.isatty():
        return None

    def show(position: int, total: int, doc_id: str) -> None:
        sys.stderr.write(f"\r  [{position:>{len(str(total))}}/{total}] {doc_id:<24}")
        sys.stderr.flush()

    return show


def _clear_progress() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 40 + "\r")
        sys.stderr.flush()


def _counts(counter: Counter[str], order: tuple[str, ...]) -> str:
    names = [name for name in order if counter.get(name)] + sorted(set(counter) - set(order))
    return " | ".join(f"{name} {counter[name]}" for name in names) or "none"


def format_summary(summary: Summary, settings: IngestSettings) -> str:
    """The run in a few lines of counts. Holds ids, codes and paths, never document content."""
    noun = "document" if summary.documents == 1 else "documents"
    scope = f"requested {noun}" if summary.targeted else noun
    sources = ", ".join(
        f"{summary.sources[name]} {label}" for name, label in _SOURCE_LABELS.items() if summary.sources.get(name)
    )
    warnings = (
        f"{summary.documents_with_warnings} documents: {_counts(summary.warnings, ())}"
        if summary.warnings
        else "none"
    )
    lines = [
        f"Ingest: {summary.documents} {scope} in {summary.seconds:.1f} s "
        f"({summary.processed} processed, {summary.reused} taken from the cache)",
        f"  kinds      {_counts(summary.kinds, _KIND_ORDER)}",
        f"  families   {_counts(summary.families, (*FAMILIES, 'unknown'))}",
        f"  pages      {summary.pages}" + (f" ({sources})" if sources else ""),
        f"  warnings   {warnings}",
        f"  errors     {_counts(summary.errors, ()) if summary.errors else 'none'}",
        f"  ignored    {summary.ignored} files or folders (system or junk files)",
        f"  catalogue  {summary.catalogue_rows_without_file} rows without a file",
        f"  index      {settings.display(summary.index_path)}",
    ]
    lines += [f"  note       {notice.code.value}: {notice.message}" for notice in summary.notices]
    return "\n".join(lines)


def format_problems(result: RunResult) -> str:
    lines = []
    for record in result.records:
        codes = [notice.code.value for notice in record.warnings]
        if record.error:
            codes.insert(0, f"ERROR {record.error.code.value}")
        if codes:
            lines.append(f"  {record.id:<16} {', '.join(codes)}")
    return "\n".join(lines) if lines else "  no document has a warning or an error"


if __name__ == "__main__":
    raise SystemExit(main())
