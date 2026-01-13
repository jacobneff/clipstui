from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .fileops_plan import Operation, OperationPlan, OperationType


class ApplyStatus(Enum):
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ApplyResult:
    operation: Operation
    status: ApplyStatus
    message: str | None = None


@dataclass(frozen=True)
class ApplyReport:
    results: list[ApplyResult]

    @property
    def ok_count(self) -> int:
        return sum(1 for result in self.results if result.status == ApplyStatus.OK)

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status == ApplyStatus.ERROR)

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.results if result.status == ApplyStatus.SKIPPED)


@dataclass(frozen=True)
class _MoveStep:
    source: Path
    target: Path
    origin: Operation
    is_dir: bool


@dataclass(frozen=True)
class _MoveItem:
    source: Path
    target: Path
    origin: Operation
    is_dir: bool


def apply_plan(plan: OperationPlan) -> ApplyReport:
    results: list[ApplyResult] = []
    creates = [
        op
        for op in plan.operations
        if op.op_type in {OperationType.CREATE_DIR, OperationType.CREATE_FILE}
    ]
    moves = [op for op in plan.operations if op.op_type == OperationType.MOVE]
    deletes = [op for op in plan.operations if op.op_type == OperationType.DELETE]

    for op in creates:
        results.append(_apply_create(op))

    move_results = _apply_moves(moves)
    results.extend(move_results)

    delete_files = [op for op in deletes if not op.is_dir]
    delete_dirs = [op for op in deletes if op.is_dir]
    for op in delete_files + delete_dirs:
        results.append(_apply_delete(op))

    return ApplyReport(results=results)


def _apply_create(op: Operation) -> ApplyResult:
    if op.target is None:
        return ApplyResult(op, ApplyStatus.ERROR, "Missing target path.")
    try:
        if op.op_type == OperationType.CREATE_DIR:
            if op.target.exists():
                if op.target.is_dir():
                    return ApplyResult(op, ApplyStatus.SKIPPED, "Directory already exists.")
                return ApplyResult(op, ApplyStatus.ERROR, "Target exists and is not a directory.")
            op.target.mkdir(parents=True, exist_ok=False)
            return ApplyResult(op, ApplyStatus.OK, None)
        if op.op_type == OperationType.CREATE_FILE:
            if op.target.exists():
                return ApplyResult(op, ApplyStatus.ERROR, "Target file already exists.")
            op.target.parent.mkdir(parents=True, exist_ok=True)
            op.target.touch(exist_ok=False)
            return ApplyResult(op, ApplyStatus.OK, None)
    except OSError as exc:
        return ApplyResult(op, ApplyStatus.ERROR, str(exc))
    return ApplyResult(op, ApplyStatus.ERROR, "Unsupported create operation.")


def _apply_delete(op: Operation) -> ApplyResult:
    if op.source is None:
        return ApplyResult(op, ApplyStatus.ERROR, "Missing source path.")
    try:
        if op.is_dir:
            op.source.rmdir()
        else:
            op.source.unlink()
    except OSError as exc:
        return ApplyResult(op, ApplyStatus.ERROR, str(exc))
    return ApplyResult(op, ApplyStatus.OK, None)


def _apply_moves(moves: list[Operation]) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    move_steps, skipped = _order_moves(moves)
    results.extend(skipped)

    skipped_ids = {id(result.operation) for result in skipped}
    status_by_id: dict[int, ApplyResult] = {
        id(op): ApplyResult(op, ApplyStatus.OK, None) for op in moves if id(op) not in skipped_ids
    }

    for step in move_steps:
        current = status_by_id.get(id(step.origin))
        if current is None or current.status == ApplyStatus.ERROR:
            continue
        try:
            _apply_move_step(step.source, step.target, step.is_dir)
        except OSError as exc:
            status_by_id[id(step.origin)] = ApplyResult(step.origin, ApplyStatus.ERROR, str(exc))

    results.extend(status_by_id.values())
    return results


def _apply_move_step(source: Path, target: Path, is_dir: bool) -> None:
    if not target.parent.exists():
        raise OSError("Target parent does not exist.")
    if target.exists():
        if is_dir:
            raise OSError("Target directory already exists.")
        if target.is_dir():
            raise OSError("Target is an existing directory.")
        os.replace(source, target)
        return
    try:
        source.rename(target)
    except OSError:
        shutil.move(str(source), str(target))


def _order_moves(moves: list[Operation]) -> tuple[list[_MoveStep], list[ApplyResult]]:
    pending: list[_MoveItem] = []
    skipped: list[ApplyResult] = []
    for op in moves:
        if op.source is None or op.target is None:
            skipped.append(ApplyResult(op, ApplyStatus.ERROR, "Missing source or target."))
            continue
        if _path_key(op.source) == _path_key(op.target):
            skipped.append(ApplyResult(op, ApplyStatus.SKIPPED, "Source and target are the same."))
            continue
        pending.append(_MoveItem(op.source, op.target, op, op.is_dir))

    steps: list[_MoveStep] = []
    source_keys = {_path_key(item.source) for item in pending}
    existing_keys = source_keys | {_path_key(item.target) for item in pending}

    while pending:
        progress = False
        for item in list(pending):
            if _path_key(item.target) not in source_keys:
                steps.append(_MoveStep(item.source, item.target, item.origin, item.is_dir))
                pending.remove(item)
                source_keys.discard(_path_key(item.source))
                progress = True
        if progress:
            continue
        item = pending.pop(0)
        source_keys.discard(_path_key(item.source))
        temp = _unique_temp_path(item.source, existing_keys)
        existing_keys.add(_path_key(temp))
        steps.append(_MoveStep(item.source, temp, item.origin, item.is_dir))
        pending.append(_MoveItem(temp, item.target, item.origin, item.is_dir))
        source_keys.add(_path_key(temp))

    return steps, skipped


def _unique_temp_path(source: Path, existing: set[str]) -> Path:
    base = f"{source.name}.clipstui_tmp"
    candidate = source.parent / base
    counter = 1
    while _path_key(candidate) in existing or candidate.exists():
        candidate = source.parent / f"{base}_{counter}"
        counter += 1
    return candidate


def _path_key(path: Path) -> str:
    return str(path).casefold()
