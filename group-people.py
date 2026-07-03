#!/usr/bin/env python3
"""
Sort messy files into tidy per-person name folders.

Move files whose names embed a person's name (e.g. '2024.john.smith.report.pdf')
into a destination subfolder named in 'First Last' form. Names are matched against
the folders already present in the destination: confident matches move
automatically, borderline matches ask for confirmation. A dry run is performed
unless --move is given.

The --suggest mode instead reports unmatched files grouped by a best-guess name
that does not yet exist in the destination. The --noise mode lists the most common
filename tokens not already in group-people.noise.txt, the editable list of cruft
skipped while guessing names.
"""

from __future__ import annotations

import argparse
import errno
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

# A corpus-guessed name pair must appear in at least this many source files.
MIN_PAIR_FILES = 2

# Fraction of all source files above which a recurring pair is cruft, not a name.
MAX_NAME_SHARE = 1 / 3

# Token prefix length used to block names for fuzzy candidate generation.
PREFIX_LEN = 3

# Splits a name into its lowercase alphanumeric parts.
TOKEN_RE = re.compile(r"[a-z0-9]+")

# A bare four-digit year, treated as noise when guessing names.
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# Number of candidate tokens listed by the --noise diagnostic.
TOP_TOKENS = 50

# Editable list of filename cruft, kept beside this script and loaded at startup.
NOISE_FILENAME = "group-people.noise.txt"


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
    position: int           # Leftmost file-token index the name matched.


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


@dataclass(frozen=True)
class Corpus:
    """
    File counts of tokens and adjacent token pairs across the source filenames.
    """
    token_df: dict[str, int]                # Token -> number of files holding it.
    pair_df: dict[tuple[str, str], int]     # Adjacent pair -> number of files.
    total: int                              # Number of files counted.


@dataclass(frozen=True)
class NameIndex:
    """
    Destination names with lookup tables that narrow matching to a few candidates.

    Without this every file would be scored against every name; instead each file
    only scores names that share an exact token or a token prefix with it.
    """
    names: list[str]                    # Destination folder names.
    tokens: list[list[str]]             # Tokenised names, parallel to `names`.
    by_exact: dict[str, list[int]]      # Token -> indices of names using it.
    by_prefix: dict[str, list[int]]     # Token prefix -> indices of names using it.


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

    parser.add_argument("-m", "--move", action="store_true",
        help="actually move files (default: dry run)")
    parser.add_argument("-s", "--suggest", action="store_true",
        help="report unmatched files grouped by a guessed new name, then exit")
    parser.add_argument("--noise", action="store_true",
        help=f"list the most common filename tokens not in {NOISE_FILENAME} "
        f"(use --noise=N for the top N, default {TOP_TOKENS}), then exit")

    borderline = parser.add_mutually_exclusive_group()
    borderline.add_argument("-y", "--yes", action="store_true",
        help="move borderline matches without asking")
    borderline.add_argument("-n", "--no", action="store_true",
        help="skip borderline matches without asking")
    parser.add_argument("-a", "--all", action="store_true", dest="show_all",
        help="include hidden source files")
    parser.add_argument("--overwrite", action="store_true",
        help="replace existing destination files instead of skipping them")

    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
        metavar="N", help=f"score (0-100) below which there is no match "
        f"(default: {DEFAULT_MIN_SCORE:g})")
    parser.add_argument("--auto-score", type=float, default=DEFAULT_AUTO_SCORE,
        metavar="N", help=f"score (0-100) at/above which an adjacent match moves "
        f"automatically (default: {DEFAULT_AUTO_SCORE:g})")

    # --noise carries an optional attached count (--noise=N); pull it out before
    # parsing so a following source path is never mistaken for the count.
    noise_count = TOP_TOKENS
    scrubbed: list[str] = []
    for arg in args:
        if arg.startswith("--noise="):
            value = arg.removeprefix("--noise=")
            if not value.isdigit():
                parser.error(f"--noise count must be a non-negative integer, "
                    f"not {value!r}")
            noise_count = int(value)
            scrubbed.append("--noise")
        else:
            scrubbed.append(arg)

    namespace = parser.parse_args(scrubbed)
    namespace.noise_count = noise_count
    return namespace


def tokenize(text: str) -> list[str]:
    """
    Return the lowercase alphanumeric tokens of a string.
    """
    return TOKEN_RE.findall(text.lower())


def name_tokens(stem: str, noise: frozenset[str]) -> list[str]:
    """
    Tokenise a filename stem, dropping years, bare numbers, and noise words.
    """
    return [t for t in tokenize(stem) if len(t) >= 2
        and not t.isdigit() and not YEAR_RE.match(t) and t not in noise]


def name_pairs(tokens: list[str]) -> list[tuple[str, str]]:
    """
    Return the adjacent token pairs whose tokens are both alphabetic.
    """
    return [(a, b) for a, b in zip(tokens, tokens[1:]) if a.isalpha() and b.isalpha()]


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


def load_noise_tokens(path: Path) -> frozenset[str]:
    """
    Load the editable cruft list, warning with instructions if it is missing.

    The file holds one token per line; blank lines and text after '#' are
    ignored. A missing file leaves no tokens marked as noise.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        warn(f"no noise list at {path}; treating every token as significant\n"
            "  run with --noise to list common tokens, then save them there "
            "(one per line)")
        return frozenset()
    tokens: set[str] = set()
    for line in text.splitlines():
        tokens.update(tokenize(line.split("#", 1)[0]))
    return frozenset(tokens)


def score_name(file_tokens: list[str], wanted: list[str]) -> tuple[float, bool, int]:
    """
    Score how well the wanted name tokens appear among the file tokens.

    Each wanted token is greedily paired with its best unused file token. The
    returned score is the weakest such pairing, so every part of the name must be
    present for a high score. The boolean reports whether the matched tokens were
    consecutive and in order, and the integer is the leftmost file-token index the
    name matched.
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
    start = min((p for p in positions if p >= 0), default=len(file_tokens))
    return score, adjacent, start


def build_index(names: list[str]) -> NameIndex:
    """
    Tokenise the destination names once and index them for candidate lookup.

    Each name is keyed by its first and last token, both exactly and by prefix,
    so a file can find the few names worth scoring without a full scan.
    """
    tokens = [tokenize(name) for name in names]
    by_exact: dict[str, list[int]] = {}
    by_prefix: dict[str, list[int]] = {}
    for index, parts in enumerate(tokens):
        if not parts:
            continue
        for token in {parts[0], parts[-1]}:
            by_exact.setdefault(token, []).append(index)
            by_prefix.setdefault(token[:PREFIX_LEN], []).append(index)
    return NameIndex(names, tokens, by_exact, by_prefix)


def candidates_for(file_tokens: list[str], index: NameIndex) -> set[int]:
    """
    Return the indices of names sharing an exact token or prefix with the file.

    These are the only names worth scoring; a name with no shared token cannot
    reach the match threshold (barring a typo in both of its parts).
    """
    found: set[int] = set()
    for token in set(file_tokens):
        found.update(index.by_exact.get(token, ()))
        found.update(index.by_prefix.get(token[:PREFIX_LEN], ()))
    return found


def build_match(path: Path, index: NameIndex,
        min_score: float, auto_score: float) -> Match:
    """
    Resolve a single source file to its best destination match and tier.

    When several confident names score almost equally, the one appearing earliest
    in the filename wins; the match is only ambiguous when two such names share
    that earliest position.
    """
    file_tokens = tokenize(path.stem)
    candidates = [
        Candidate(index.names[i], *score_name(file_tokens, index.tokens[i]))
        for i in candidates_for(file_tokens, index)]
    if not candidates:
        return Match(path=path, name=None, score=0.0, tier=Tier.NONE)

    best_score = max(c.score for c in candidates)
    if best_score < min_score:
        return Match(path=path, name=None, score=best_score, tier=Tier.NONE)

    contenders = [c for c in candidates
        if c.score >= min_score and best_score - c.score < AMBIGUITY_MARGIN]
    contenders.sort(key=lambda c: (c.position, not c.adjacent, -c.score))
    top = contenders[0]

    ambiguous = any(c is not top and c.position == top.position for c in contenders)
    if top.score >= auto_score and top.adjacent and not ambiguous:
        tier = Tier.AUTO
    else:
        tier = Tier.CONFIRM
    return Match(path=path, name=top.name, score=top.score, tier=tier)


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


def build_corpus(files: list[Path], noise: frozenset[str]) -> Corpus:
    """
    Count how many files each token and each adjacent pair appears in.

    Counting once per file makes a recurring name pair stand out: a person's name
    appears across all of their files while the surrounding cruft varies.
    """
    token_df: dict[str, int] = {}
    pair_df: dict[tuple[str, str], int] = {}
    for path in files:
        tokens = name_tokens(path.stem, noise)
        for token in set(tokens):
            token_df[token] = token_df.get(token, 0) + 1
        for pair in set(name_pairs(tokens)):
            pair_df[pair] = pair_df.get(pair, 0) + 1
    return Corpus(token_df, pair_df, len(files))


def likely_name_pair(pairs: list[tuple[str, str]],
        corpus: Corpus) -> tuple[str, str] | None:
    """
    Pick the pair that most plausibly names a person, or None when none does.

    A pair must recur across files to count as name evidence, yet one present in
    too large a share of all files is cruft: one person rarely owns most of the
    pile. Among the rest, a pair scores its file count over the commoner token's
    file count, so a recurring name unit beats a cruft word whose partner also
    turns up elsewhere. The latest pair wins ties, as cruft precedes names.
    """
    best = -1.0
    chosen: tuple[str, str] | None = None
    for pair in pairs:
        count = corpus.pair_df.get(pair, 0)
        if count < MIN_PAIR_FILES or count > corpus.total * MAX_NAME_SHARE:
            continue
        denom = max(corpus.token_df.get(pair[0], 1), corpus.token_df.get(pair[1], 1))
        score = count / denom
        if score >= best:               # Not strict, so the latest pair wins ties.
            best = score
            chosen = pair
    return chosen


def guess_name(stem: str, vocab: Vocabulary | None = None,
        noise: frozenset[str] = frozenset(),
        corpus: Corpus | None = None) -> str | None:
    """
    Best-guess a 'First Last' name from a filename, or None if none looks likely.

    Years, bare numbers, single characters, and common cruft are dropped to leave
    candidate adjacent token pairs. Given a vocabulary, the pair whose tokens best
    fit known first/last name positions wins, and an apparently reversed
    'Last First' pair is flipped. When no name part is known, a corpus picks the
    pair whose tokens most exclusively co-occur across the source files, or None
    when no recurring, name-like pair exists. The latest pair wins ties, as noise
    tends to precede the name in real filenames.
    """
    tokens = name_tokens(stem, noise)
    pairs = name_pairs(tokens)
    if not pairs:
        return None

    vocab_score = -1
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
            if score >= vocab_score:    # Not strict, so the latest pair wins ties.
                vocab_score = score
                first, last = ordered

    # With no known name part, recurrence across files beats filename position.
    if vocab_score < 1 and corpus is not None:
        pair = likely_name_pair(pairs, corpus)
        if pair is None:
            return None
        first, last = pair
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


def print_match(match: Match, width: int, replacing: bool = False) -> None:
    """
    Print one colour-coded line describing the planned action for a file.
    """
    name = match.path.name.ljust(width)
    if match.tier is Tier.AUTO:
        body = f"move  {name}  ->  {match.name}/   ({match.score:.0f})"
    elif match.tier is Tier.CONFIRM:
        body = f"ask   {name}  ->  {match.name}?   ({match.score:.0f})"
    else:
        body = f"--    {name}  (no match)"
    if replacing:
        body += "  [replaces existing]"
    colour = colorama.Fore.RED if replacing else colour_for(match.tier)
    print(colour + body + colorama.Style.RESET_ALL)


def confirm(question: str) -> bool:
    """
    Ask a yes/no question, defaulting to no on EOF or anything but yes.
    """
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def move_file(path: Path, dest: Path, name: str, overwrite: bool = False) -> bool:
    """
    Move a file into the destination's named subfolder, safely across filesystems.

    An interrupted move leaves no partial file under the final name and no leftover
    temporary. Returns False without moving if a file of the same name already
    exists there, unless overwrite is set.
    """
    folder = dest / name
    target = folder / path.name
    if target.exists() and not overwrite:
        warn(f"skipped (target exists): {target}")
        return False
    folder.mkdir(exist_ok=True)
    try:
        path.replace(target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        part = target.with_name(target.name + ".part")
        try:
            shutil.copy2(path, part)
            part.replace(target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        path.unlink()
    return True


def move_match(match: Match, dest: Path,
        assume_yes: bool, assume_no: bool, overwrite: bool = False) -> bool:
    """
    Carry out one planned move, prompting on or skipping a borderline match.

    A borderline match is moved without asking when assume_yes is set, skipped
    without asking when assume_no is set, and otherwise confirmed interactively.
    Returns True when the file is moved, False when it is skipped or unmatched.
    """
    name = match.name
    if name is None:
        return False
    if match.tier is Tier.CONFIRM and not assume_yes:
        if assume_no or not confirm(f"Move {match.path.name!r} into {name!r}?"):
            return False
    return move_file(match.path, dest, name, overwrite)


def run_noise(files: list[Path], noise: frozenset[str], limit: int) -> int:
    """
    List the most common filename tokens not already in the noise list.

    Tokens are counted once per file, so the ranking reflects how many files use
    each word; cruft shared across many files rises to the top while names stay
    rare. Years, bare numbers, and single characters are left out. The bare tokens
    print to stdout ready to paste into the noise file, with the file count shown
    as a strippable comment on the first and last entries only.
    """
    counts: dict[str, int] = {}
    for path in files:
        for token in set(name_tokens(path.stem, noise)):
            counts[token] = counts.get(token, 0) + 1

    if not counts:
        print(f"no candidate tokens found in {len(files)} file(s)", file=sys.stderr)
        return 0

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    print(f"most common tokens not in {NOISE_FILENAME} (showing {len(ranked)}, "
        "by file count) -- paste in and delete the names:", file=sys.stderr)
    last = len(ranked) - 1
    for index, (token, count) in enumerate(ranked):
        comment = f"  # {count}" if index in (0, last) else ""
        print(f"{token}{comment}")
    return 0


def run_suggest(matches: list[Match], names: list[str],
        noise: frozenset[str]) -> int:
    """
    Print unmatched files grouped by a guessed name not already in the destination.
    """
    existing = {name.lower() for name in names}
    vocab = build_vocabulary(names)
    corpus = build_corpus([match.path for match in matches], noise)
    groups: dict[str, list[Path]] = {}
    no_guess: list[Path] = []
    for match in matches:
        if match.tier is not Tier.NONE:
            continue
        guess = guess_name(match.path.stem, vocab, noise, corpus)
        if guess is None or guess.lower() in existing:
            no_guess.append(match.path)
            continue
        groups.setdefault(guess, []).append(match.path)

    if groups:
        print("Suggested new folders (not yet in the destination):\n")
        # Fewest matches first so the names with the most files end up last.
        for name in sorted(groups, key=lambda n: (len(groups[n]), n)):
            print(colorama.Fore.CYAN + f"{name} ({len(groups[name])})"
                + colorama.Style.RESET_ALL)
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
    noise = load_noise_tokens(Path(__file__).resolve().parent / NOISE_FILENAME)
    files = source_files(source, options.show_all)

    if options.noise:
        return run_noise(files, noise, options.noise_count)

    names = known_names(dest)
    index = build_index(names)
    min_score: float = options.min_score
    auto_score: float = options.auto_score
    total = len(files)

    if options.suggest:
        suggest_matches: list[Match] = []
        for number, path in enumerate(files, start=1):
            suggest_matches.append(build_match(path, index, min_score, auto_score))
            if number % 1000 == 0:
                print(f"  scanned {number}/{total}...", file=sys.stderr)
        return run_suggest(suggest_matches, names, noise)

    if not names:
        warn(f"no name folders found in {dest}; try --suggest")

    # Match, print, and (when moving) act on each file in turn, so output and
    # moves stream out together instead of stalling on large sets.
    width = max((len(path.name) for path in files), default=0)
    moved = skipped = replaced = auto = ask = none = 0
    interrupted = False
    try:
        for path in files:
            match = build_match(path, index, min_score, auto_score)
            replacing = (options.overwrite and match.name is not None
                and (dest / match.name / path.name).exists())
            print_match(match, width, replacing)
            if match.tier is Tier.AUTO:
                auto += 1
            elif match.tier is Tier.CONFIRM:
                ask += 1
            else:
                none += 1
            if options.move and match.name is not None:
                if move_match(match, dest, options.yes, options.no, options.overwrite):
                    moved += 1
                    if replacing:
                        replaced += 1
                else:
                    skipped += 1
    except KeyboardInterrupt:
        interrupted = True
        print()
        warn("interrupted; no file was left partially moved")

    if options.move:
        summary = f"moved {moved}, skipped {skipped}, {none} unmatched"
        if options.overwrite:
            summary += f" ({replaced} replaced)"
    else:
        summary = (f"{auto} to move, {ask} to confirm, {none} unmatched "
            f"({total} files) (DRY RUN)")
    print(colorama.Fore.YELLOW + summary + colorama.Style.RESET_ALL,
        file=sys.stderr)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
