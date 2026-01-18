from __future__ import annotations

from pathlib import Path

from clipstui.fileops_apply import apply_plan
from clipstui.fileops_plan import Operation, OperationPlan, OperationType


def test_apply_plan_swaps_files(tmp_path: Path) -> None:
    root = tmp_path
    a_path = root / "a.txt"
    b_path = root / "b.txt"
    a_path.write_text("A")
    b_path.write_text("B")

    operations = [
        Operation(OperationType.MOVE, source=a_path, target=b_path, is_dir=False),
        Operation(OperationType.MOVE, source=b_path, target=a_path, is_dir=False),
    ]
    plan = OperationPlan(
        root=root,
        operations=operations,
        original_entries=[],
        edited_entries=[],
        delete_markers=[],
        parse_errors=[],
    )

    report = apply_plan(plan)

    assert report.error_count == 0
    assert a_path.read_text() == "B"
    assert b_path.read_text() == "A"


def test_apply_plan_deletes_non_empty_directory(tmp_path: Path) -> None:
    root = tmp_path
    dir_path = root / "folder"
    dir_path.mkdir()
    nested = dir_path / "nested.txt"
    nested.write_text("hello")

    operations = [Operation(OperationType.DELETE, source=dir_path, is_dir=True)]
    plan = OperationPlan(
        root=root,
        operations=operations,
        original_entries=[],
        edited_entries=[],
        delete_markers=[],
        parse_errors=[],
    )

    report = apply_plan(plan)

    assert report.error_count == 0
    assert not dir_path.exists()
