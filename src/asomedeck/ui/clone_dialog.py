"""Pick a folder, give an address, get a deck.

Not modal, and the only dialog in the app that is not. Every other one asks a question that
takes a moment, while this one can sit there for as long as a large repository takes to arrive,
and a modal dialog would hold every terminal in the window hostage for all of it. So it floats
above the window, one at a time, and the terminals carry on.

The folder is asked for first and comes filled in, because most repositories on a machine live
side by side, and the address is what the cursor starts in. The clone goes in a new folder
inside the one picked, named after the repository until somebody says otherwise.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import git_clone


class CloneDialog(QDialog):
    """The form, and the one clone it starts."""

    #: The clone landed here. The dialog closes itself straight after.
    cloned = Signal(str)

    def __init__(self, folder: str, git: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CloneDialog")
        self.setWindowTitle("Clone repository")
        self.setMinimumWidth(560)
        self._git = git
        self._job: git_clone.CloneJob | None = None
        #: Whether the name is still the one worked out from the address. Typing in the field
        #: ends that, and emptying it hands the job back.
        self._name_follows_url = True

        self.folder = QLineEdit(folder)
        self.browse = QPushButton("Browse…")
        self.browse.setObjectName("CloneBrowse")
        self.browse.setAutoDefault(False)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://github.com/owner/repository.git")
        self.name = QLineEdit()

        where = QHBoxLayout()
        where.setSpacing(8)
        where.addWidget(self.folder, 1)
        where.addWidget(self.browse)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Clone into", where)
        form.addRow("Repository", self.url)
        form.addRow("Folder name", self.name)

        self.status = QLabel()
        self.status.setObjectName("CloneStatus")
        self.status.setWordWrap(True)
        # A failure is git's own words, and somebody may well want to search for them.
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()

        self.buttons = QDialogButtonBox()
        self.start_button = self.buttons.addButton(
            "Clone", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_button.setDefault(True)
        self.cancel_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        self.browse.clicked.connect(self._browse)
        self.folder.textEdited.connect(self._refresh)
        self.url.textEdited.connect(self._on_url_edited)
        self.name.textEdited.connect(self._on_name_edited)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)

        self.url.setFocus()
        self._refresh()

    # --- the form ----------------------------------------------------------------------

    @property
    def cloning(self) -> bool:
        return self._job is not None and self._job.running

    def _destination(self) -> str:
        return os.path.join(self._folder_text(), self.name.text().strip())

    def _folder_text(self) -> str:
        text = self.folder.text().strip()
        return os.path.normpath(text) if text else ""

    def _browse(self) -> None:
        start = self._folder_text() or os.path.expanduser("~")
        picked = QFileDialog.getExistingDirectory(self, "Clone into", start)
        if picked:
            self.folder.setText(os.path.normpath(picked))
            self._refresh()

    def _on_url_edited(self) -> None:
        if self._name_follows_url:
            self.name.setText(git_clone.repo_folder_name(self.url.text()))
        self._refresh()

    def _on_name_edited(self) -> None:
        self._name_follows_url = not self.name.text()
        if self._name_follows_url:
            self.name.setText(git_clone.repo_folder_name(self.url.text()))
        self._refresh()

    def _problem(self) -> str | None:
        return (git_clone.url_problem(self.url.text())
                or git_clone.destination_problem(self._folder_text(), self.name.text().strip())
                or git_clone.same_place_problem(self.url.text(), self._destination()))

    def _refresh(self) -> None:
        """Say where this would go, or why it cannot, before anybody presses anything."""
        problem = self._problem()
        self.start_button.setEnabled(problem is None)
        if problem is None:
            self._say(f"Creates {self._destination()}")
        elif not self.url.text().strip():
            # An empty form has nothing wrong with it yet. It just has not been filled in.
            self._say("")
        else:
            self._say(problem, bad=True)

    def _say(self, text: str, bad: bool = False) -> None:
        self.status.setText(text)
        self.status.setProperty("tone", "bad" if bad else "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _set_editable(self, editable: bool) -> None:
        for widget in (self.folder, self.browse, self.url, self.name):
            widget.setEnabled(editable)

    # --- the clone ---------------------------------------------------------------------

    def _start(self) -> None:
        if self.cloning or self._problem() is not None:
            return
        self._set_editable(False)
        self.start_button.setEnabled(False)
        self.progress.setRange(0, 0)  # busy, until git reports a percentage
        self.progress.show()
        self._say("Starting…")
        job = git_clone.CloneJob(self)
        job.progress.connect(self._on_progress)
        job.succeeded.connect(self._on_succeeded)
        job.failed.connect(self._on_failed)
        job.cancelled.connect(self._on_cancelled)
        self._job = job
        job.start(self.url.text(), self._destination(), self._git)

    def _on_progress(self, phase: str, percent: int) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(percent)
        if self.cancel_button.isEnabled():
            self._say(f"{phase}: {percent}%")

    def _on_succeeded(self, destination: str) -> None:
        self.cloned.emit(destination)
        self.accept()

    def _on_failed(self, message: str) -> None:
        """Back to the form, with everything still in it. The address was probably a typo."""
        self.progress.hide()
        self._set_editable(True)
        self.start_button.setEnabled(True)
        self._say(message, bad=True)

    def _on_cancelled(self) -> None:
        super().reject()

    def reject(self) -> None:
        """Cancel, Esc and the title bar's close all come here.

        During a clone that means stop it, and the dialog closes once that is done rather than
        now. Going away first would leave nothing on screen to say a folder is still being
        cleared, and a second try at the same address would find it in the way.
        """
        if not self.cloning:
            super().reject()
            return
        self.cancel_button.setEnabled(False)
        self._say("Cancelling…")
        self._job.cancel()

    def abandon(self) -> None:
        """The app is quitting. Stop now and clear up before returning."""
        if self.cloning:
            self._job.cancel(wait=True)
