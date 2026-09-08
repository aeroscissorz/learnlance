"""Find where a topic shows up in a codebase and build a compact code blob.

Used by `learnlance add <topic>`: rather than trust the model to have file
access, we locate the relevant lines ourselves (zero dependencies, cross
platform) and feed just those snippets to the same insight extractor the hook
uses. This keeps the "search the code and add it" flow deterministic.
"""
from __future__ import annotations

import re
from pathlib import Path

# Directories we never want to walk into.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", "dist", "build", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages", ".next", ".cache",
    "target", "coverage", ".tox",
}
# Only read files that are plausibly source/text.
_TEXT_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".java", ".go",
    ".rs", ".rb", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".html",
    ".css", ".scss", ".less", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".md", ".txt", ".sh", ".bash", ".zsh", ".sql", ".swift", ".kt",
    ".scala", ".lua", ".r", ".m", ".mm", ".vue", ".svelte", ".dart", ".ex",
    ".exs", ".clj", ".hs", ".pl", ".ps1",
}
_MAX_FILE_BYTES = 400_000  # skip anything huge (generated/minified/vendored)
_STOPWORDS = {"the", "and", "for", "with", "from", "into", "how", "why", "use"}


def _terms(topic: str) -> list[str]:
    words = [w for w in re.split(r"[^A-Za-z0-9_]+", topic.lower()) if len(w) > 2]
    words = [w for w in words if w not in _STOPWORDS]
    return words or [topic.lower().strip()]


def _iter_files(root: Path):
    # os.walk-style traversal with in-place dir pruning.
    # `seen` holds resolved directory paths: p.is_dir() follows symlinks, so a
    # link pointing at an ancestor would otherwise make this loop forever.
    stack = [root]
    seen: set[str] = set()
    while stack:
        d = stack.pop()
        try:
            key = str(d.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            entries = list(d.iterdir())
        except (OSError, PermissionError):
            continue
        for p in entries:
            try:
                if p.is_dir():
                    if p.name not in _SKIP_DIRS and not p.name.startswith("."):
                        stack.append(p)
                elif p.is_file() and p.suffix.lower() in _TEXT_EXT:
                    yield p
            except OSError:
                continue


def _hit_lines(low: list[str], terms: list[str], strict: bool = True) -> list[int]:
    """Line indices matching `terms`.

    `strict` requires every term on the same line (or the phrase as written);
    otherwise any single term is enough. See `gather` for why the choice is made
    across the whole tree rather than per file.
    """
    if not terms:
        return []
    if strict and len(terms) > 1:
        phrase = " ".join(terms)
        return [i for i, ln in enumerate(low)
                if phrase in ln or all(t in ln for t in terms)]
    return [i for i, ln in enumerate(low) if any(t in ln for t in terms)]


def _merge_ranges(indices: list[int], ctx: int, n: int) -> list[tuple[int, int]]:
    """Turn hit line indices into merged [start, end) ranges with context."""
    ranges: list[tuple[int, int]] = []
    for i in indices:
        a, b = max(0, i - ctx), min(n, i + ctx + 1)
        if ranges and a <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], b))
        else:
            ranges.append((a, b))
    return ranges


def gather(topic: str, root: str | Path, max_chars: int = 14000,
           ctx: int = 4, max_files: int = 40) -> tuple[str, list[str]]:
    """Return (code_blob, matched_files) for `topic` under `root`.

    The blob is capped at ~max_chars and formatted with file headers + line
    numbers so the model can cite where the concept appears.
    """
    root = Path(root)
    terms = _terms(topic)

    # Two passes, because precision has to be decided across the whole tree, not
    # per file: `add "delta encoding"` should not pull in every file mentioning
    # "encoding" just because that file has no line containing both words. Only
    # if *nothing* anywhere matches all the terms do we widen to any-term.
    blob, files = _scan(root, terms, strict=True, max_chars=max_chars,
                        ctx=ctx, max_files=max_files)
    if files or len(terms) < 2:
        return blob, files
    return _scan(root, terms, strict=False, max_chars=max_chars,
                 ctx=ctx, max_files=max_files)


def _scan(root: Path, terms: list[str], strict: bool, max_chars: int,
          ctx: int, max_files: int) -> tuple[str, list[str]]:
    chunks: list[str] = []
    files_hit: list[str] = []
    total = 0

    for path in _iter_files(root):
        if len(files_hit) >= max_files or total >= max_chars:
            break
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        low = [ln.lower() for ln in lines]
        hits = _hit_lines(low, terms, strict=strict)
        if not hits:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        seg = [f"FILE {rel}:"]
        for a, b in _merge_ranges(hits, ctx, len(lines)):
            seg.append(f"  lines {a + 1}-{b}:")
            seg.extend(f"    {lines[i]}" for i in range(a, b))
        block = "\n".join(seg)
        if total + len(block) > max_chars:
            block = block[: max(0, max_chars - total)]
        if not block:
            break
        chunks.append(block)
        files_hit.append(str(rel))
        total += len(block)

    return "\n\n".join(chunks), files_hit
