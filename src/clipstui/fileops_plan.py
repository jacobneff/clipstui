from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path

DELETE_MARKER = "[DELETE]"

_INVALID_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class OperationType(Enum):
    CREATE_FILE = "create_file"
    CREATE_DIR = "create_dir"
    MOVE = "move"
    DELETE = "delete"


@dataclass(frozen=True)
class PathEntry:
    path: Path
    is_dir: bool


@dataclass(frozen=True)
class EditedEntry:
    raw: str
    path: Path
    rel: Path
    norm: str
    is_dir_hint: bool


@dataclass(frozen=True)
class DeleteMarker:
    raw: str
    path: Path
    rel: Path
    norm: str


@dataclass(frozen=True)
class Operation:
    op_type: OperationType
    source: Path | None = None
    target: Path | None = None
    is_dir: bool = False


@dataclass(frozen=True)
class ValidationError:
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class Confirmation:
    operation: Operation
    reason: str


@dataclass(frozen=True)
class OperationPlan:
    root: Path
    operations: list[Operation]
    original_entries: list[PathEntry]
    edited_entries: list[EditedEntry]
    delete_markers: list[DeleteMarker]
    parse_errors: list[ValidationError]


def compute_plan(
    root: Path,
    original_entries: list[PathEntry],
    edited_lines: list[str],
) -> OperationPlan:
    root_resolved = root.resolve()
    original_norms = [
        _normalize_rel(_rel_to_root(root_resolved, entry.path)) for entry in original_entries
    ]
    parse_errors: list[ValidationError] = []
    edited_entries: list[EditedEntry] = []
    delete_markers: list[DeleteMarker] = []

    for raw in edited_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if _is_delete_marker(stripped):
            marker = _parse_delete_marker(root_resolved, stripped, parse_errors)
            if marker is not None:
                delete_markers.append(marker)
            continue
        entry = _parse_edited_entry(root_resolved, raw, parse_errors)
        if entry is not None:
            edited_entries.append(entry)

    edited_norms = [entry.norm for entry in edited_entries]
    matcher = SequenceMatcher(a=original_norms, b=edited_norms, autojunk=False)

    state = ["keep"] * len(original_entries)
    move_target: list[int | None] = [None] * len(original_entries)
    create_indices: list[int] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for idx in range(i1, i2):
                state[idx] = "delete"
            continue
        if tag == "insert":
            create_indices.extend(range(j1, j2))
            continue
        if tag == "replace":
            orig_block = list(range(i1, i2))
            edit_block = list(range(j1, j2))
            pair_count = min(len(orig_block), len(edit_block))
            for offset in range(pair_count):
                orig_idx = orig_block[offset]
                edit_idx = edit_block[offset]
                state[orig_idx] = "move"
                move_target[orig_idx] = edit_idx
            for idx in orig_block[pair_count:]:
                state[idx] = "delete"
            create_indices.extend(edit_block[pair_count:])

    norm_to_index = {norm: idx for idx, norm in enumerate(original_norms)}
    edited_norm_set = {entry.norm for entry in edited_entries}
    for marker in delete_markers:
        idx = norm_to_index.get(marker.norm)
        if idx is None:
            parse_errors.append(
                ValidationError("Delete marker does not match existing entry.", marker.path)
            )
            continue
        if marker.norm in edited_norm_set:
            parse_errors.append(
                ValidationError("Delete marker conflicts with an edited entry.", marker.path)
            )
            continue
        if state[idx] == "move":
            parse_errors.append(
                ValidationError("Delete marker conflicts with a rename/move.", marker.path)
            )
            continue
        state[idx] = "delete"

    operations: list[Operation] = []
    for idx, entry in enumerate(original_entries):
        if state[idx] == "move":
            target_idx = move_target[idx]
            if target_idx is None:
                continue
            target_entry = edited_entries[target_idx]
            if _path_key(entry.path) == _path_key(target_entry.path):
                continue
            operations.append(
                Operation(
                    OperationType.MOVE,
                    source=entry.path,
                    target=target_entry.path,
                    is_dir=entry.is_dir,
                )
            )
        elif state[idx] == "delete":
            operations.append(
                Operation(
                    OperationType.DELETE,
                    source=entry.path,
                    is_dir=entry.is_dir,
                )
            )

    for idx in create_indices:
        entry = edited_entries[idx]
        op_type = OperationType.CREATE_DIR if entry.is_dir_hint else OperationType.CREATE_FILE
        operations.append(Operation(op_type, target=entry.path, is_dir=entry.is_dir_hint))

    return OperationPlan(
        root=root_resolved,
        operations=operations,
        original_entries=original_entries,
        edited_entries=edited_entries,
        delete_markers=delete_markers,
        parse_errors=parse_errors,
    )


def validate_plan(plan: OperationPlan) -> list[ValidationError]:
    errors = list(plan.parse_errors)
    root = plan.root.resolve()

    edited_keys: dict[str, Path] = {}
    for entry in plan.edited_entries:
        key = _path_key(entry.path)
        if key in edited_keys:
            errors.append(
                ValidationError("Duplicate edited path.", entry.path)
            )
        else:
            edited_keys[key] = entry.path
        name_error = _validate_relative_path(root, entry.path)
        if name_error:
            errors.append(ValidationError(name_error, entry.path))

    target_keys: dict[str, Operation] = {}
    for op in plan.operations:
        if op.target is None:
            continue
        key = _path_key(op.target)
        if key in target_keys:
            errors.append(ValidationError("Duplicate target path.", op.target))
        else:
            target_keys[key] = op

    planned_dirs = _planned_dirs(root, plan.original_entries, plan.operations)
    for op in plan.operations:
        if op.source and not _is_within_root(root, op.source):
            errors.append(ValidationError("Source is outside the current root.", op.source))
        if op.target and not _is_within_root(root, op.target):
            errors.append(ValidationError("Target is outside the current root.", op.target))

        if op.op_type in {OperationType.MOVE, OperationType.CREATE_FILE, OperationType.CREATE_DIR}:
            if op.target is None:
                continue
            parent = op.target.parent
            parent_key = _path_key(parent)
            if parent_key not in planned_dirs and not parent.exists():
                errors.append(ValidationError("Target parent does not exist.", op.target))

        if op.op_type == OperationType.MOVE and op.source and op.target:
            if op.is_dir and _is_relative_to(op.target, op.source):
                errors.append(ValidationError("Cannot move a directory into itself.", op.target))
            if op.target.exists():
                if op.is_dir:
                    errors.append(ValidationError("Target directory already exists.", op.target))
                elif op.target.is_dir():
                    errors.append(ValidationError("Target is an existing directory.", op.target))

        if op.op_type == OperationType.CREATE_FILE and op.target:
            if op.target.exists():
                errors.append(ValidationError("Target file already exists.", op.target))

        if op.op_type == OperationType.CREATE_DIR and op.target:
            if op.target.exists() and not op.target.is_dir():
                errors.append(ValidationError("Target exists and is not a directory.", op.target))

    return errors


def collect_confirmations(plan: OperationPlan) -> list[Confirmation]:
    confirmations: list[Confirmation] = []
    for op in plan.operations:
        if op.op_type == OperationType.DELETE:
            confirmations.append(Confirmation(op, "Confirm delete"))
        elif op.op_type == OperationType.MOVE:
            if op.target and op.target.exists() and op.source:
                if not op.is_dir and not op.target.is_dir():
                    confirmations.append(Confirmation(op, "Confirm overwrite"))
        elif op.op_type == OperationType.CREATE_DIR:
            continue
        elif op.op_type == OperationType.CREATE_FILE:
            continue
    return confirmations


def _parse_edited_entry(
    root: Path, raw: str, errors: list[ValidationError]
) -> EditedEntry | None:
    text = raw.strip()
    is_dir_hint = text.endswith(("/", "\\"))
    text = text.rstrip("/\\")
    if not text:
        errors.append(ValidationError("Edited line is empty after trimming.", None))
        return None
    try:
        path = Path(text)
    except OSError as exc:
        errors.append(ValidationError(f"Invalid path: {exc}.", None))
        return None
    if not path.is_absolute():
        path = root / path
    path = path.resolve(strict=False)
    try:
        rel = path.relative_to(root)
    except ValueError:
        errors.append(ValidationError("Path escapes the current root.", path))
        return None
    if not rel.parts:
        errors.append(ValidationError("Path resolves to the root directory.", path))
        return None
    norm = _normalize_rel(rel)
    return EditedEntry(raw=raw, path=path, rel=rel, norm=norm, is_dir_hint=is_dir_hint)


def _parse_delete_marker(
    root: Path, raw: str, errors: list[ValidationError]
) -> DeleteMarker | None:
    remainder = raw[len(DELETE_MARKER) :].strip()
    if not remainder:
        errors.append(ValidationError("Delete marker missing a path.", None))
        return None
    entry = _parse_edited_entry(root, remainder, errors)
    if entry is None:
        return None
    return DeleteMarker(raw=raw, path=entry.path, rel=entry.rel, norm=entry.norm)


def _is_delete_marker(value: str) -> bool:
    return value.upper().startswith(DELETE_MARKER)


def _normalize_rel(rel: Path) -> str:
    return rel.as_posix().rstrip("/")


def _rel_to_root(root: Path, path: Path) -> Path:
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(root)
    except ValueError:
        return Path(resolved.name)


def _path_key(path: Path) -> str:
    return str(path).casefold()


def _is_within_root(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return False
    return True


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(base.resolve(strict=False))
    except ValueError:
        return False
    return True


def _planned_dirs(root: Path, original: list[PathEntry], operations: list[Operation]) -> set[str]:
    dirs: set[str] = {_path_key(root)}
    for entry in original:
        if entry.is_dir:
            dirs.add(_path_key(entry.path.resolve(strict=False)))
    for op in operations:
        if op.op_type != OperationType.CREATE_DIR or op.target is None:
            continue
        current = op.target.resolve(strict=False)
        while True:
            dirs.add(_path_key(current))
            if current == root or current.parent == current:
                break
            current = current.parent
    return dirs


def _validate_relative_path(root: Path, path: Path) -> str | None:
    try:
        rel = path.resolve(strict=False).relative_to(root)
    except ValueError:
        return "Path escapes the current root."
    if not rel.parts:
        return "Path resolves to the root directory."
    for part in rel.parts:
        message = _invalid_component(part)
        if message:
            return message
    return None


def _invalid_component(part: str) -> str | None:
    if not part or part in {".", ".."}:
        return "Path component is invalid."
    if part[-1] in {" ", "."}:
        return "Path component ends with a space or dot."
    for char in part:
        if char in _INVALID_CHARS or ord(char) < 32:
            return "Path component contains invalid characters."
    trimmed = part.rstrip(" .")
    if not trimmed:
        return "Path component is invalid."
    base = trimmed.split(".")[0].upper()
    if base in _RESERVED_NAMES:
        return "Path component uses a reserved name."
    return None


def is_delete_marker_line(line: str) -> bool:
    return line.strip().upper().startswith(DELETE_MARKER)


def strip_delete_marker(line: str) -> str:
    if not is_delete_marker_line(line):
        return line
    remainder = line.strip()[len(DELETE_MARKER) :].lstrip()
    return remainder


def toggle_delete_marker(line: str) -> str:
    text = line.rstrip("\n")
    if not text.strip():
        return text
    if is_delete_marker_line(text):
        return strip_delete_marker(text)
    return f"{DELETE_MARKER} {text.strip()}"
