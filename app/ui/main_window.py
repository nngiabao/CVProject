from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.emulators.base import EmulatorProvider
from app.models import EmulatorInstance, InstanceState, ProxyConfig
from app.proxy_parser import parse_proxy_text


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.instances: list[EmulatorInstance] = []
        self.proxies: list[ProxyConfig] = []
        self.assignments: dict[int, ProxyConfig] = {}
        self.proxy_cursor = 0

        self.setWindowTitle("Emulator Proxy Manager")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self.refresh_instances()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(3000)
        self.refresh_timer.timeout.connect(self.refresh_instances)
        self.refresh_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_content(), 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(self.provider.display_name)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)

        title_box = QVBoxLayout()
        title = QLabel("Emulator Proxy Manager")
        title.setObjectName("title")
        subtitle = QLabel("LDPlayer fleet control and per-instance proxy assignment")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch()

        self.total_metric = QLabel("Total: 0")
        self.running_metric = QLabel("Running: 0")
        self.assigned_metric = QLabel("Assigned: 0")
        for metric in (self.total_metric, self.running_metric, self.assigned_metric):
            metric.setObjectName("metric")
            layout.addWidget(metric)

        refresh = QPushButton("Refresh")
        refresh.setObjectName("primary")
        refresh.clicked.connect(self.refresh_instances)
        layout.addWidget(refresh)
        return frame

    def _build_toolbar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("toolbar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)

        select_all = QPushButton("Select all")
        select_all.clicked.connect(self.table_select_all)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_selection)
        assign = QPushButton("Assign proxies")
        assign.setObjectName("primary")
        assign.clicked.connect(self.assign_proxies)
        self.assignment_mode = QComboBox()
        self.assignment_mode.addItem("Same proxy to selected", "same")
        self.assignment_mode.addItem("Different proxies", "different")
        start = QPushButton("Start")
        start.setObjectName("success")
        start.clicked.connect(lambda: self._run_selected(self.provider.start, "started"))
        stop = QPushButton("Stop")
        stop.setObjectName("danger")
        stop.clicked.connect(lambda: self._run_selected(self.provider.stop, "stopped"))
        restart = QPushButton("Restart")
        restart.clicked.connect(lambda: self._run_selected(self.provider.restart, "restarted"))
        route = QPushButton("Start proxy routing")
        route.setToolTip("Available after the WinDivert routing service is implemented")
        route.setEnabled(False)

        for button in (select_all, clear, assign):
            layout.addWidget(button)
        layout.addWidget(self.assignment_mode)
        for button in (start, stop, restart, route):
            layout.addWidget(button)
        layout.addStretch()
        return frame

    def _build_content(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_instance_panel())
        splitter.addWidget(self._build_proxy_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([820, 390])
        return splitter

    def _build_instance_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel("Emulator instances")
        heading.setObjectName("metric")
        layout.addWidget(heading)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["#", "Platform", "Instance", "PID", "State", "Proxy status", "Proxy"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        return frame

    def _build_proxy_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)

        heading = QLabel("Proxy configuration")
        heading.setObjectName("metric")
        layout.addWidget(heading)
        help_text = QLabel("One proxy per line. Credentials are masked in the table.")
        help_text.setObjectName("muted")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["socks5", "http", "https", "socks4", "socks4a"])
        layout.addWidget(self.proxy_type)

        self.proxy_editor = QPlainTextEdit()
        self.proxy_editor.setPlaceholderText(
            "127.0.0.1:1080\n"
            "user:password@proxy.example.com:1080\n"
            "socks5://user:password@10.0.0.2:1080"
        )
        layout.addWidget(self.proxy_editor, 1)

        button_row = QHBoxLayout()
        load = QPushButton("Load proxies")
        load.setObjectName("primary")
        load.clicked.connect(self.load_proxies)
        clear = QPushButton("Clear proxies")
        clear.clicked.connect(self.clear_proxies)
        button_row.addWidget(load)
        button_row.addWidget(clear)
        layout.addLayout(button_row)

        self.proxy_summary = QLabel("Loaded: 0")
        self.proxy_summary.setObjectName("muted")
        layout.addWidget(self.proxy_summary)
        return frame

    def refresh_instances(self) -> None:
        try:
            fresh_instances = self.provider.list_instances()
        except Exception as exc:
            QMessageBox.critical(self, "LDPlayer error", str(exc))
            return

        for instance in fresh_instances:
            assigned = self.assignments.get(instance.index)
            instance.proxy = assigned.display if assigned else None
        self.instances = fresh_instances
        self._render_instances()
        self.statusBar().showMessage(self.provider.display_name)

    def _render_instances(self) -> None:
        self.table.setRowCount(len(self.instances))
        for row, instance in enumerate(self.instances):
            assigned = self.assignments.get(instance.index)
            values = (
                str(instance.index),
                instance.platform,
                instance.name,
                str(instance.pid or "—"),
                instance.state.value,
                "Assigned" if assigned else "Unassigned",
                assigned.display if assigned else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, instance.index)
                if column == 4:
                    state_colors = {
                        InstanceState.RUNNING: "#43cf78",
                        InstanceState.STARTING: "#f0b84b",
                        InstanceState.STOPPED: "#8f97ad",
                        InstanceState.UNKNOWN: "#ef6c72",
                    }
                    color = state_colors[instance.state]
                    item.setForeground(QColor(color))
                if column == 5:
                    item.setForeground(QColor("#a787ff" if assigned else "#8f97ad"))
                self.table.setItem(row, column, item)

        running = sum(item.state == InstanceState.RUNNING for item in self.instances)
        self.total_metric.setText(f"Total: {len(self.instances)}")
        self.running_metric.setText(f"Running: {running}")
        self.assigned_metric.setText(f"Assigned: {len(self.assignments)}")

    def table_select_all(self) -> None:
        self.table.selectAll()

    def clear_selection(self) -> None:
        self.table.clearSelection()

    def selected_indexes(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.instances[row].index for row in rows]

    def load_proxies(self) -> None:
        proxies, errors = parse_proxy_text(self.proxy_editor.toPlainText(), self.proxy_type.currentText())
        self.proxies = proxies
        self.proxy_cursor = 0
        summary = f"Loaded: {len(proxies)}"
        if errors:
            summary += f" · Invalid: {len(errors)}"
            QMessageBox.warning(self, "Some proxies were skipped", "\n".join(errors[:10]))
        self.proxy_summary.setText(summary)
        self.statusBar().showMessage(summary, 5000)

    def clear_proxies(self) -> None:
        self.proxies.clear()
        self.assignments.clear()
        self.proxy_cursor = 0
        self.proxy_editor.clear()
        self.proxy_summary.setText("Loaded: 0")
        self._render_instances()

    def assign_proxies(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return
        if not self.proxies:
            self.load_proxies()
        if not self.proxies:
            return

        mode = self.assignment_mode.currentData()
        if mode == "same":
            proxy = self.proxies[self.proxy_cursor % len(self.proxies)]
            for instance_index in indexes:
                self.assignments[instance_index] = proxy
            self.proxy_cursor += 1
        else:
            for position, instance_index in enumerate(indexes):
                proxy_index = (self.proxy_cursor + position) % len(self.proxies)
                self.assignments[instance_index] = self.proxies[proxy_index]
            self.proxy_cursor += len(indexes)
        self._render_instances()
        self.statusBar().showMessage(f"Assigned proxies to {len(indexes)} instance(s)", 5000)

    def _run_selected(self, action: Callable[[int], None], verb: str) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        failures: list[str] = []
        for index in indexes:
            try:
                action(index)
            except Exception as exc:
                failures.append(f"Instance {index}: {exc}")
        self.refresh_instances()

        if failures:
            QMessageBox.warning(self, "Some actions failed", "\n".join(failures))
        else:
            self.statusBar().showMessage(f"{len(indexes)} instance(s) {verb}", 5000)
