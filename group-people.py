#!/usr/bin/env python3
"""
Sort messy files into tidy per-person name folders.

Move files whose names embed a person's name (e.g. '2024.john.smith.report.pdf')
into a destination subfolder named in 'First Last' form. Names are matched against
the folders already present in the destination: confident matches move
automatically, borderline matches ask for confirmation. A dry run is performed
unless --force is given.

The --suggest mode instead reports unmatched files grouped by a best-guess name
that does not yet exist in the destination.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

import colorama  # type: ignore[import-untyped]

try:
    from rapidfuzz import fuzz
except ModuleNotFoundError:
    sys.exit(
        "error: this script needs the 'rapidfuzz' package, which is not installed.\n"
        "  venv:   python3 -m venv .venv && .venv/bin/pip install rapidfuzz\n"
        "  user:   python3 -m pip install --user --break-system-packages rapidfuzz\n"
        "  apt:    sudo apt install python3-rapidfuzz"
    )

# Fuzzy score (0-100) at/above which an adjacent match moves without asking.
DEFAULT_AUTO_SCORE = 88.0

# Fuzzy score (0-100) below which a file is treated as having no match.
DEFAULT_MIN_SCORE = 80.0

# A clear winner must beat the runner-up name by this margin to move on its own.
AMBIGUITY_MARGIN = 4.0

# Splits a name into its lowercase alphanumeric parts.
TOKEN_RE = re.compile(r"[a-z0-9]+")

# A bare four-digit year, treated as noise when guessing names.
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# Common filename cruft skipped when guessing a name in --suggest mode.
NOISE_TOKENS = frozenset({
    "copy", "doc", "document", "draft", "file", "final", "img", "image",
    "jpg", "pdf", "png", "scan", "scanned", "signed", "v", "version",
})


class Tier(Enum):
    """
    Confidence band a file's best match falls into.
    """
    AUTO = "auto"           # Move without asking.
    CONFIRM = "confirm"     # Ask the user first.
    NONE = "none"           # No acceptable match.


@dataclass
class Candidate:
    """
    One destination name scored against a single source file.
    """
    name: str               # Destination folder name, e.g. "John Smith".
    score: float            # Combined fuzzy score, 0-100.
    adjacent: bool          # Name tokens matched consecutive file tokens in order.


@dataclass
class Match:
    """
    The chosen destination for a single source file.
    """
    path: Path              # Source file.
    name: str | None        # Destination folder name, or None when unmatched.
    score: float            # Score of the chosen candidate, 0-100.
    tier: Tier              # Confidence band.


@dataclass(frozen=True)
class Vocabulary:
    """
    First and last name tokens seen across the destination folder names.
    """
    firsts: frozenset[str]  # Leading tokens, e.g. "john" from "John Smith".
    lasts: frozenset[str]   # Trailing tokens, e.g. "smith" from "John Smith".


def parse_arguments(args: list[str]) -> argparse.Namespace:
    """
    Create and run the argparse-based command parser.
    """
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("source", type=Path,
        help="folder holding the messy source files")
    parser.add_argument("dest", type=Path,
        help="folder holding the tidy 'First Last' subfolders")

    parser.add_argument("-f", "--force", action="store_true",
        help="actually move files (default: dry run)")
    parser.add_argument("-y", "--yes", action="store_true",
        help="auto-confirm borderline matches instead of prompting")
    parser.add_argument("-s", "--suggest", action="store_true",
        help="report unmatched files grouped by a guessed new name, then exit")
    parser.add_argument("-a", "--all", action="store_true", dest="show_all",
        help="include hidden source files")

    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
        metavar="N", help=f"score (0-100) below which there is no match "
        f"(default: {DEFAULT_MIN_SCORE:g})")
    parser.add_argument("--auto-score", type=float, default=DEFAULT_AUTO_SCORE,
        metavar="N", help=f"score (0-100) at/above which an adjacent match moves "
        f"automatically (default: {DEFAULT_AUTO_SCORE:g})")

    return parser.parse_args(args)


def tokenize(text: str) -> list[str]:
    """
    Return the lowercase alphanumeric tokens of a string.
    """
    return TOKEN_RE.findall(text.lower())


def known_names(dest: Path) -> list[str]:
    """
    Return the names of existing subfolders in the destination, sorted.
    """
    return sorted(entry.name for entry in dest.iterdir() if entry.is_dir())


def source_files(source: Path, show_all: bool) -> list[Path]:
    """
    Return the files directly inside the source folder, sorted by name.
    """
    files = []
    for entry in sorted(source.iterdir()):
        if not entry.is_file():
            continue
        if not show_all and entry.name.startswith("."):
            continue
        files.append(entry)
    return files


def score_name(file_tokens: list[str], wanted: list[str]) -> tuple[float, bool]:
    """
    Score how well the wanted name tokens appear among the file tokens.

    Each wanted token is greedily paired with its best unused file token. The
    returned score is the weakest such pairing, so every part of the name must be
    present for a high score. The boolean reports whether the matched tokens were
    consecutive and in order.
    """
    used: set[int] = set()
    positions: list[int] = []
    per_token: list[float] = []
    for want in wanted:
        best_score = 0.0
        best_index = -1
        for index, token in enumerate(file_tokens):
            if index in used:
                continue
            ratio = fuzz.ratio(want, token)
            if ratio > best_score:
                best_score = ratio
                best_index = index
        per_token.append(best_score)
        positions.append(best_index)
        if best_index >= 0:
            used.add(best_index)

    score = min(per_token) if per_token else 0.0
    adjacent = len(positions) >= 2 and all(p >= 0 for p in positions) and all(
        positions[i] + 1 == positions[i + 1] for i in range(len(positions) - 1))
    return score, adjacent


def classify(file_tokens: list[str], names: list[str],
        min_score: float, auto_score: float) -> Match | None:
    """
    Pick the best destination name for a file and its confidence tier.

    Returns None when there are no destination names to match against.
    """
    if not names:
        return None

    candidates = [Candidate(name, *score_name(file_tokens, tokenize(name)))
        for name in names]
    candidates.sort(key=lambda c: (c.score, c.adjacent), reverse=True)
    top = candidates[0]

    if top.score < min_score:
        return Match(path=Path(), name=None, score=top.score, tier=Tier.NONE)

    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    ambiguous = (runner_up >= min_score
        and (top.score - runner_up) < AMBIGUITY_MARGIN)

    if top.score >= auto_score and top.adjacent and not ambiguous:
        tier = Tier.AUTO
    else:
        tier = Tier.CONFIRM
    return Match(path=Path(), name=top.name, score=top.score, tier=tier)


def build_match(path: Path, names: list[str],
        min_score: float, auto_score: float) -> Match:
    """
    Resolve a single source file to its best destination match.
    """
    result = classify(tokenize(path.stem), names, min_score, auto_score)
    if result is None:
        return Match(path=path, name=None, score=0.0, tier=Tier.NONE)
    result.path = path
    return result


def build_vocabulary(names: list[str]) -> Vocabulary:
    """
    Collect the known first and last name tokens from destination folder names.
    """
    firsts: set[str] = set()
    lasts: set[str] = set()
    for name in names:
        tokens = tokenize(name)
        if tokens:
            firsts.add(tokens[0])
            lasts.add(tokens[-1])
    return Vocabulary(frozenset(firsts), frozenset(lasts))


def guess_name(stem: str, vocab: Vocabulary | None = None) -> str | None:
    """
    Best-guess a 'First Last' name from a filename, or None if none looks likely.

    Years, bare numbers, single characters, and common cruft are dropped to leave
    candidate adjacent token pairs. Given a vocabulary, the pair whose tokens best
    fit known first/last name positions wins, and an apparently reversed
    'Last First' pair is flipped; otherwise the first pair is taken. The earliest
    pair wins ties, so a leading noise word never displaces a real name.
    """
    tokens = [t for t in tokenize(stem) if len(t) >= 2
        and not t.isdigit() and not YEAR_RE.match(t) and t not in NOISE_TOKENS]
    pairs = [(a, b) for a, b in zip(tokens, tokens[1:]) if a.isalpha() and b.isalpha()]
    if not pairs:
        return None

    best_score = -1
    first, last = pairs[0]
    if vocab is not None:
        for left, right in pairs:
            # A correctly-placed known name outweighs a misplaced (reversed) one.
            forward = 2 * (left in vocab.firsts) + 2 * (right in vocab.lasts)
            reverse = (right in vocab.firsts) + (left in vocab.lasts)
            if reverse > forward:       # Only flip on stronger reversed evidence.
                score, ordered = reverse, (right, left)
            else:
                score, ordered = forward, (left, right)
            if score > best_score:      # Strict, so the earliest pair wins ties.
                best_score = score
                first, last = ordered
    return f"{first.title()} {last.title()}"


def colour_for(tier: Tier) -> str:
    """
    Return the colorama colour used to print a match in the given tier.
    """
    colours: dict[Tier, str] = {
        Tier.AUTO: colorama.Fore.GREEN,
        Tier.CONFIRM: colorama.Fore.YELLOW,
        Tier.NONE: colorama.Style.DIM,
    }
    return colours[tier]


def preview(matches: list[Match]) -> None:
    """
    Print every planned action, one colour-coded line per source file.
    """
    width = max((len(m.path.name) for m in matches), default=0)
    for match in matches:
        name = match.path.name.ljust(width)
        if match.tier is Tier.AUTO:
            body = f"move  {name}  ->  {match.name}/   ({match.score:.0f})"
        elif match.tier is Tier.CONFIRM:
            body = f"ask   {name}  ->  {match.name}?   ({match.score:.0f})"
        else:
            body = f"--    {name}  (no match)"
        print(colour_for(match.tier) + body + colorama.Style.RESET_ALL)


def confirm(question: str) -> bool:
    """
    Ask a yes/no question, defaulting to no on EOF or anything but yes.
    """
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def move_file(path: Path, dest: Path, name: str) -> bool:
    """
    Move a file into the destination's named subfolder.

    Returns False without moving if a file of the same name already exists there.
    """
    folder = dest / name
    target = folder / path.name
    if target.exists():
        warn(f"skipped (target exists): {target}")
        return False
    folder.mkdir(exist_ok=True)
    shutil.move(str(path), str(target))
    return True


def execute(matches: list[Match], dest: Path, assume_yes: bool) -> tuple[int, int]:
    """
    Carry out the planned moves, prompting on borderline matches.

    Returns a (moved, skipped) count pair.
    """
    moved = skipped = 0
    for match in matches:
        if match.name is None:
            continue
        if match.tier is Tier.CONFIRM and not assume_yes:
            if not confirm(f"Move {match.path.name!r} into {match.name!r}?"):
                skipped += 1
                continue
        if move_file(match.path, dest, match.name):
            moved += 1
        else:
            skipped += 1
    return moved, skipped


def run_suggest(matches: list[Match], names: list[str]) -> int:
    """
    Print unmatched files grouped by a guessed name not already in the destination.
    """
    existing = {name.lower() for name in names}
    vocab = build_vocabulary(names)
    groups: dict[str, list[Path]] = {}
    no_guess: list[Path] = []
    for match in matches:
        if match.tier is not Tier.NONE:
            continue
        guess = guess_name(match.path.stem, vocab)
        if guess is None or guess.lower() in existing:
            no_guess.append(match.path)
            continue
        groups.setdefault(guess, []).append(match.path)

    if groups:
        print("Suggested new folders (not yet in the destination):\n")
        for name in sorted(groups):
            print(colorama.Fore.CYAN + name + colorama.Style.RESET_ALL)
            for path in groups[name]:
                print(f"    {path.name}")
        print()

    if no_guess:
        print(f"{len(no_guess)} unmatched file(s) with no confident name guess.")

    print(f"\n{len(groups)} suggested new name(s).", file=sys.stderr)
    return 0


def warn(message: str) -> None:
    """
    Print a warning to stderr.
    """
    print(colorama.Fore.RED + f"warning: {message}" + colorama.Style.RESET_ALL,
        file=sys.stderr)


def main() -> int:
    """
    Parse arguments, build the move plan, and either preview or carry it out.
    """
    options = parse_arguments(sys.argv[1:])
    source: Path = options.source
    dest: Path = options.dest

    if not source.is_dir():
        print(f"error: not a directory: {source}", file=sys.stderr)
        return 2
    if not dest.is_dir():
        print(f"error: not a directory: {dest}", file=sys.stderr)
        return 2

    colorama.init()
    names = known_names(dest)
    files = source_files(source, options.show_all)
    matches = [build_match(f, names, options.min_score, options.auto_score)
        for f in files]

    if options.suggest:
        return run_suggest(matches, names)

    if not names:
        warn(f"no name folders found in {dest}; try --suggest")

    preview(matches)

    auto = sum(1 for m in matches if m.tier is Tier.AUTO)
    ask = sum(1 for m in matches if m.tier is Tier.CONFIRM)
    none = sum(1 for m in matches if m.tier is Tier.NONE)

    if options.force:
        moved, skipped = execute(matches, dest, options.yes)
        summary = f"moved {moved}, skipped {skipped}, {none} unmatched"
    else:
        summary = (f"{auto} to move, {ask} to confirm, {none} unmatched "
            f"({len(files)} files) (DRY RUN)")
    print(colorama.Fore.YELLOW + summary + colorama.Style.RESET_ALL,
        file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
