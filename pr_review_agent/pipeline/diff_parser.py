"""Parse PR diffs into structured format."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiffHunk:
    """A single hunk from a diff."""
    start_line_old: int
    start_line_new: int
    lines: list[str]
    header: str = ""

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for added lines."""
        result = []
        current_line = self.start_line_new
        for line in self.lines:
            if line.startswith("+"):
                result.append((current_line, line[1:]))
                current_line += 1
            elif line.startswith("-"):
                pass  # deleted line, don't increment new line counter
            else:
                current_line += 1
        return result

    @property
    def removed_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for removed lines."""
        result = []
        current_line = self.start_line_old
        for line in self.lines:
            if line.startswith("-"):
                result.append((current_line, line[1:]))
                current_line += 1
            elif line.startswith("+"):
                pass
            else:
                current_line += 1
        return result


@dataclass
class FileDiff:
    """Parsed diff for a single file."""
    file_path: str
    old_path: str | None = None
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    raw_diff: str = ""

    @property
    def language(self) -> str:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python",
            ".go": "go",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".rb": "ruby",
            ".java": "java",
            ".kt": "kotlin",
            ".scala": "scala",
            ".rs": "rust",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".php": "php",
        }
        for ext, lang in ext_map.items():
            if self.file_path.endswith(ext):
                return lang
        return "unknown"

    @property
    def total_added(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def total_removed(self) -> int:
        return sum(len(h.removed_lines) for h in self.hunks)


def parse_diff(raw_diff: str) -> list[FileDiff]:
    """Parse a unified diff into structured FileDiff objects.

    Args:
        raw_diff: Full unified diff string.

    Returns:
        List of FileDiff objects, one per changed file.
    """
    files = []
    current_file = None
    current_hunk = None

    for line in raw_diff.split("\n"):
        # New file header
        if line.startswith("diff --git"):
            if current_file is not None:
                if current_hunk:
                    current_file.hunks.append(current_hunk)
                files.append(current_file)

            # Parse file paths from "diff --git a/path b/path"
            parts = line.split(" ")
            a_path = parts[2][2:] if len(parts) > 2 else ""  # strip "a/"
            b_path = parts[3][2:] if len(parts) > 3 else a_path  # strip "b/"

            current_file = FileDiff(file_path=b_path, old_path=a_path)
            current_hunk = None

        elif line.startswith("new file"):
            if current_file:
                current_file.is_new = True

        elif line.startswith("deleted file"):
            if current_file:
                current_file.is_deleted = True

        elif line.startswith("@@"):
            # Hunk header: @@ -old_start,old_count +new_start,new_count @@
            if current_file is not None:
                if current_hunk:
                    current_file.hunks.append(current_hunk)

                old_start, new_start = _parse_hunk_header(line)
                current_hunk = DiffHunk(
                    start_line_old=old_start,
                    start_line_new=new_start,
                    lines=[],
                    header=line,
                )

        elif current_hunk is not None:
            # Skip binary file markers and --- / +++ headers
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("Binary files"):
                continue
            current_hunk.lines.append(line)

    # Don't forget the last file/hunk
    if current_file is not None:
        if current_hunk:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    # Attach raw diff segments
    _attach_raw_diffs(files, raw_diff)

    return files


def _parse_hunk_header(header: str) -> tuple[int, int]:
    """Parse @@ -old_start,count +new_start,count @@ header."""
    import re
    match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1, 1


def _attach_raw_diffs(files: list[FileDiff], raw_diff: str) -> None:
    """Attach raw diff segments to each FileDiff."""
    segments = raw_diff.split("diff --git")
    for i, segment in enumerate(segments[1:], 0):
        if i < len(files):
            files[i].raw_diff = "diff --git" + segment


def filter_reviewable_files(files: list[FileDiff]) -> list[FileDiff]:
    """Filter out non-reviewable files (configs, assets, lock files, etc.)."""
    skip_patterns = [
        ".lock", ".sum", ".mod", "package-lock.json", "yarn.lock",
        ".min.js", ".min.css", ".map",
        ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico",
        ".woff", ".woff2", ".ttf", ".eot",
        ".pdf", ".zip", ".tar", ".gz",
        "vendor/", "node_modules/", "dist/", "build/",
        ".gitignore", ".gitattributes",
        "LICENSE", "CHANGELOG", "AUTHORS",
    ]

    reviewable_languages = {
        "python", "go", "typescript", "javascript", "ruby", "java",
        "kotlin", "scala", "rust", "c", "cpp", "csharp", "php",
    }

    result = []
    for f in files:
        # Skip deleted files
        if f.is_deleted:
            continue
        # Skip non-code files
        if any(p in f.file_path for p in skip_patterns):
            continue
        # Prefer known languages
        if f.language in reviewable_languages or f.total_added > 0:
            result.append(f)

    return result
