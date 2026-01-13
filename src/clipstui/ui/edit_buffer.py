from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ..fileops_apply import ApplyReport
from ..fileops_plan import Confirmation, Operation, OperationPlan, OperationType


class PlanPreviewScreen(ModalScreen[bool]):
    CSS = """
    PlanPreviewScreen {
        align: center middle;
        background: $surface 80%;
    }

    #preview_dialog {
        width: 80%;
        height: 70%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #preview_text {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self, root: Path, plan: OperationPlan, confirmations: list[Confirmation]) -> None:
        super().__init__()
        self._root = root
        self._plan = plan
        self._confirmations = confirmations
        self._buttons: list[Button] = []
        self._focus_index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="preview_dialog"):
            yield Static(
                _format_plan(self._root, self._plan, self._confirmations),
                id="preview_text",
            )
            with Horizontal():
                apply_button = Button("Yes (Y)", id="preview_apply")
                cancel_button = Button("No (N)", id="preview_cancel")
                self._buttons = [apply_button, cancel_button]
                yield apply_button
                yield cancel_button

    def on_mount(self) -> None:
        if self._buttons:
            self._buttons[0].focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"y", "Y", "enter", "return"}:
            self.dismiss(True)
            event.stop()
        elif event.key in {"n", "N", "escape"}:
            self.dismiss(False)
            event.stop()
        elif event.key in {"left", "up"}:
            self._move_focus(-1)
            event.stop()
        elif event.key in {"right", "down"}:
            self._move_focus(1)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview_apply":
            self.dismiss(True)
        elif event.button.id == "preview_cancel":
            self.dismiss(False)

    def _move_focus(self, delta: int) -> None:
        if not self._buttons:
            return
        self._focus_index = (self._focus_index + delta) % len(self._buttons)
        self._buttons[self._focus_index].focus()


class ApplyReportScreen(ModalScreen[None]):
    CSS = """
    ApplyReportScreen {
        align: center middle;
        background: $surface 80%;
    }

    #report_dialog {
        width: 80%;
        height: 70%;
        padding: 1 2;
        border: heavy $accent;
        background: $panel;
    }

    #report_text {
        height: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self, root: Path, report: ApplyReport) -> None:
        super().__init__()
        self._root = root
        self._report = report

    def compose(self) -> ComposeResult:
        summary = (
            f"Applied: {self._report.ok_count} ok, "
            f"{self._report.skipped_count} skipped, "
            f"{self._report.error_count} failed."
        )
        with Vertical(id="report_dialog"):
            yield Label(summary)
            yield Static(_format_report(self._root, self._report), id="report_text")
            yield Button("Close", id="report_close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "report_close":
            self.dismiss(None)


def _format_plan(root: Path, plan: OperationPlan, confirmations: list[Confirmation]) -> Text:
    text = Text()
    if not plan.operations:
        text.append("No changes detected.")
        return text
    for idx, op in enumerate(plan.operations):
        verb, style = _operation_label(op)
        text.append(verb, style=style)
        text.append(" ")
        text.append(_format_operation(root, op))
        if idx < len(plan.operations) - 1:
            text.append("\n")
    return text


def _format_report(root: Path, report: ApplyReport) -> str:
    lines: list[str] = []
    for result in report.results:
        status = result.status.value.upper()
        line = f"{status:7} {_format_operation(root, result.operation)}"
        if result.message:
            line = f"{line} | {result.message}"
        lines.append(line)
    if not lines:
        lines.append("No operations executed.")
    return "\n".join(lines)


def _format_operation(root: Path, op: Operation) -> str:
    if op.op_type == OperationType.MOVE and op.source and op.target:
        return f"{_rel(root, op.source)} -> {_rel(root, op.target)}"
    if op.op_type in {OperationType.CREATE_DIR, OperationType.CREATE_FILE} and op.target:
        return _rel(root, op.target)
    if op.op_type == OperationType.DELETE and op.source:
        return _rel(root, op.source)
    return op.op_type.value.upper()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _operation_label(op: Operation) -> tuple[str, str]:
    if op.op_type == OperationType.CREATE_DIR:
        return ("MKDIR", "green")
    if op.op_type == OperationType.CREATE_FILE:
        return ("CREATE", "green")
    if op.op_type == OperationType.DELETE:
        return ("DELETE", "red")
    if op.op_type == OperationType.MOVE:
        return ("MOVE", "yellow")
    return (op.op_type.value.upper(), "white")
