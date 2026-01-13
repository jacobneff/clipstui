from __future__ import annotations

from pathlib import Path

from clipstui.fileops_plan import (
    OperationType,
    PathEntry,
    compute_plan,
    toggle_delete_marker,
    validate_plan,
)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def test_compute_plan_rename_move_create(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dir1").mkdir()
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")

    original = [
        PathEntry(root / "dir1", True),
        PathEntry(root / "a.txt", False),
        PathEntry(root / "b.txt", False),
    ]
    edited = ["dir1/", "a_renamed.txt", "sub/b.txt", "new.txt"]

    plan = compute_plan(root, original, edited)
    moves = [op for op in plan.operations if op.op_type == OperationType.MOVE]
    creates = [op for op in plan.operations if op.op_type == OperationType.CREATE_FILE]
    deletes = [op for op in plan.operations if op.op_type == OperationType.DELETE]

    move_pairs = {
        (_rel(root, op.source), _rel(root, op.target))
        for op in moves
        if op.source and op.target
    }
    assert ("a.txt", "a_renamed.txt") in move_pairs
    assert ("b.txt", "sub/b.txt") in move_pairs
    assert len(creates) == 1
    assert creates[0].target and _rel(root, creates[0].target) == "new.txt"
    assert deletes == []


def test_compute_plan_delete_missing(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")

    original = [
        PathEntry(root / "a.txt", False),
        PathEntry(root / "b.txt", False),
    ]
    edited = ["a.txt"]

    plan = compute_plan(root, original, edited)
    deletes = [op for op in plan.operations if op.op_type == OperationType.DELETE]

    assert len(deletes) == 1
    assert deletes[0].source and deletes[0].source.name == "b.txt"


def test_compute_plan_delete_marker(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")

    original = [
        PathEntry(root / "a.txt", False),
        PathEntry(root / "b.txt", False),
    ]
    edited = ["a.txt", "[DELETE] b.txt"]

    plan = compute_plan(root, original, edited)
    deletes = [op for op in plan.operations if op.op_type == OperationType.DELETE]

    assert len(deletes) == 1
    assert deletes[0].source and deletes[0].source.name == "b.txt"


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")

    original = [
        PathEntry(root / "a.txt", False),
        PathEntry(root / "b.txt", False),
    ]
    edited = ["a.txt", "", "b.txt", "   "]

    plan = compute_plan(root, original, edited)
    deletes = [op for op in plan.operations if op.op_type == OperationType.DELETE]

    assert deletes == []


def test_toggle_delete_marker_round_trip() -> None:
    line = "folder/"
    marked = toggle_delete_marker(line)
    assert marked.startswith("[DELETE]")
    assert toggle_delete_marker(marked) == "folder/"


def test_validate_plan_catches_invalid_paths(tmp_path: Path) -> None:
    root = tmp_path
    edited = ["dup.txt", "dup.txt", "../escape.txt", "bad<name>.txt"]

    plan = compute_plan(root, [], edited)
    errors = validate_plan(plan)
    message_text = " ".join(error.message for error in errors).lower()

    assert "duplicate" in message_text
    assert "escapes" in message_text
    assert "invalid characters" in message_text


def test_validate_plan_blocks_dir_into_itself(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dir1").mkdir()
    original = [PathEntry(root / "dir1", True)]
    edited = ["dir1/subdir"]

    plan = compute_plan(root, original, edited)
    errors = validate_plan(plan)

    assert any("into itself" in error.message.lower() for error in errors)
