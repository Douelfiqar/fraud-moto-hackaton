"""official_wording: the form's own words are printed with their right letters.

An official form is printed from one master: its title and captions read the same on
every copy. A picture made by an image-generation tool imitates the look of small print,
not its letters: a caption comes out with letters swapped, fused or missing. A language
model does not see it, because it reads the word it expects. A plain text reader does:
Ingest keeps the words it reads on the page (``Page.printed_words``), and this check
looks among them for a damaged copy of one of the form's official words
(``config/official_words.toml``).

How a damaged word is told from everything else a reader returns:

- only words the reader is sure of count (``wording_min_confidence``);
- a word that contains an official word is that word, glued to its neighbour;
- the reader's usual confusions are undone first (I, l and 1; O, 0, Q and D; U and V...);
- a word cut at the edge of its box is a part of the official word: not damaged;
- a word of a dictionary is another word. Official forms repeat their title in other
  European languages ("certificado", "inmatriculare"), so several languages are loaded
  (``wording_languages``); a word one letter away from a dictionary word is that word
  misread; a word that contains a long dictionary word is real words glued together;
- what remains must be close to an official word of about the same length, with at
  least ``wording_min_wrong_letters`` wrong letters. Fewer is reported only.

The official words themselves are chosen with one constraint (``config/official_words.toml``):
no word that looks like a value a document can print. "Française" is left out because
any nationality reads like it.

The check passes only when it read at least one official word exactly: on a page where
the reader found none, it cannot tell. With the cases above handled, it fails only
where a caption is visibly garbled.
"""

from __future__ import annotations

import difflib
import re
import tomllib
import unicodedata
from functools import lru_cache
from pathlib import Path

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import doc_type

#: The text reader's usual confusions between capital letters and digits: each group is read as its first member.
_CONFUSED = ("IL1J", "O0QD", "UV", "CG", "EF", "S5", "B8", "Z2", "MN")
_FOLD = str.maketrans({member: group[0] for group in _CONFUSED for member in group[1:]})
_LETTERS = re.compile(r"[^A-Z0-9]")


def capitals(text: str) -> str:
    """Capitals without accents, letters and digits only."""
    text = unicodedata.normalize("NFKD", text)
    return _LETTERS.sub("", "".join(ch for ch in text if not unicodedata.combining(ch)).upper())


def fold(text: str) -> str:
    """The word with the reader's usual confusions undone: two words that fold alike are the same word."""
    return text.translate(_FOLD)


def wrong_letters(one: str, two: str) -> int:
    """Letters to replace, add or drop to turn one word into the other."""
    previous = list(range(len(two) + 1))
    for row, letter in enumerate(one, start=1):
        current = [row]
        for column, other in enumerate(two, start=1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (letter != other)))
        previous = current
    return previous[-1]


@lru_cache(maxsize=4)
def official_words(path: str) -> dict[str, tuple[str, ...]]:
    """Official words by document type; empty when the file is missing or cannot be read."""
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    words = data.get("words", {})
    return {name: tuple(capitals(str(word)) for word in listed) for name, listed in words.items() if isinstance(listed, list)}


@lru_cache(maxsize=2)
def dictionary(languages: tuple[str, ...]) -> frozenset[str] | None:
    """The words of these languages in capitals without accents; ``None`` when the word list is not installed."""
    try:
        from spellchecker import SpellChecker
    except ImportError:
        return None
    words: set[str] = set()
    for language in languages:
        try:
            words.update(capitals(word) for word in SpellChecker(language=language).word_frequency.words())
        except ValueError:  # a language this version of the word list does not carry
            continue
    return frozenset(words)


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: A dictionary word this long inside a longer run of letters means real words glued together by the reader.
GLUED_WORD_LETTERS = 7


def another_word(word: str, known: frozenset[str]) -> bool:
    """True when the word is a word of the dictionary, one misread by a single letter, or real words glued together."""
    if word in known:
        return True
    for place in range(len(word) + 1):
        head, tail = word[:place], word[place:]
        if tail and head + tail[1:] in known:  # one letter too many
            return True
        for letter in _ALPHABET:
            if head + letter + tail in known or (tail and head + letter + tail[1:] in known):  # one missing, one wrong
                return True
    size = len(word)
    return any(word[first:last] in known for first in range(size) for last in range(first + GLUED_WORD_LETTERS, size + 1)
               if last - first < size)


def damaged_copy(word: str, official: tuple[str, ...], known: frozenset[str], similarity: float, gap: int) -> tuple[str, int] | None:
    """The official word this printed word is a damaged copy of, and how many letters are wrong; ``None`` otherwise."""
    folded = fold(word)
    targets = [(name, fold(name)) for name in official]
    if any(target in folded or folded in target for _, target in targets):
        return None  # the official word itself, glued to a neighbour or cut at the edge of its box
    best: tuple[str, int] | None = None
    for name, target in targets:
        if abs(len(folded) - len(target)) > gap:
            continue
        if difflib.SequenceMatcher(None, folded, target).ratio() < similarity:
            continue
        wrong = wrong_letters(folded, target)
        if best is None or wrong < best[1]:
            best = (name, wrong)
    if best is not None and best[1] > 1 and another_word(word, known):
        return None  # another word of a language the form uses, not a damaged official word
    return best


@register
class OfficialWording(Check):
    name = "official_wording"
    source = "general"
    description = "Official form: none of its own words is printed with wrong letters (read by the local text reader)."

    def _official(self, context: DocumentContext) -> tuple[str, ...]:
        kind = doc_type(context)
        return official_words(str(context.settings.official_words_file)).get(kind.name, ()) if kind else ()

    def applies(self, context: DocumentContext) -> bool:
        read = any(page.printed_words is not None for page in context.record.pages)
        return read and bool(self._official(context))

    def run(self, context: DocumentContext) -> CheckResult:
        settings, official = context.settings, self._official(context)
        fields = ["libellés du formulaire"]
        known = dictionary(tuple(settings.wording_languages))
        if known is None:
            return self.result("cannot_tell", "La liste de mots (pyspellchecker) n'est pas installée : un mot abîmé ne "
                               "peut pas être distingué d'un autre mot de la langue.", fields=fields)
        folded_official = {fold(name) for name in official}
        exact: set[str] = set()
        damaged: dict[str, tuple[int, int, str]] = {}  # official word -> page, wrong letters, form read
        slight: dict[str, tuple[int, str]] = {}
        for page in context.record.pages:
            for item in page.printed_words or []:
                word = capitals(item.text)
                if item.confidence < settings.wording_min_confidence or not word:
                    continue
                exact.update(target for target in folded_official if target in fold(word))
                found = damaged_copy(word, official, known, settings.wording_min_similarity, settings.wording_max_length_gap)
                if found is None:
                    continue
                name, wrong = found
                if wrong >= settings.wording_min_wrong_letters:
                    damaged.setdefault(name, (page.number, wrong, word))
                else:
                    slight.setdefault(name, (page.number, word))
        details = {"exact": len(exact), "damaged": {name: {"page": page, "wrong_letters": wrong, "read": read}
                                                    for name, (page, wrong, read) in damaged.items()},
                   "slight": {name: {"page": page, "read": read} for name, (page, read) in slight.items()}}
        if damaged:
            listed = ", ".join(f"« {name} » (page {page})" for name, (page, _, _) in sorted(damaged.items()))
            return self.result(
                "fail",
                f"Mot(s) du formulaire officiel imprimé(s) avec des lettres fausses : {listed}. Un formulaire officiel "
                "est imprimé d'après un modèle unique ; des lettres remplacées, fusionnées ou manquantes dans ses propres "
                "libellés sont la marque d'une image redessinée par un outil de génération d'images.",
                fields=fields,
                clues=["libellé officiel imprimé avec des lettres fausses"],
                fraud_type="document généré ou redessiné par IA",
                ai_generated=True,
                details=details,
            )
        if not exact:
            return self.result("cannot_tell", "Le lecteur local n'a retrouvé aucun mot officiel du formulaire sur l'image : "
                               "ses libellés ne peuvent pas être jugés.", fields=fields, details=details)
        evidence = f"Les mots officiels du formulaire lus sur l'image ({len(exact)}) sont imprimés avec leurs lettres."
        if slight:
            evidence += (f" {len(slight)} mot(s) lu(s) avec une ou deux lettres différentes, ce qu'un lecteur fait aussi "
                         "sur une page authentique.")
        return self.result("pass", evidence, fields=fields, details=details)
