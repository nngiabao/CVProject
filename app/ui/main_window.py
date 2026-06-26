from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
        self.proxy_checks: dict[int, tuple[str, str]] = {}
        self.proxy_summary: QLabel | None = None

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
        load = QPushButton("Load SOCKS5 proxies")
        load.setObjectName("primary")
        load.clicked.connect(self.load_proxies_from_file)
        clear_proxies = QPushButton("Clear proxies")
        clear_proxies.clicked.connect(self.clear_proxies)
        self.proxy_summary = QLabel("SOCKS5 proxies: 0")
        self.proxy_summary.setObjectName("muted")
        assign = QPushButton("Assign proxy to selected")
        assign.setObjectName("primary")
        assign.clicked.connect(self.assign_proxies)
        check = QPushButton("Check selected proxies")
        check.clicked.connect(self.check_selected_proxies)
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

        for button in (select_all, clear, load, clear_proxies, assign, check):
            layout.addWidget(button)
        layout.addWidget(self.assignment_mode)
        layout.addWidget(self.proxy_summary)
        for button in (start, stop, restart, route):
            layout.addWidget(button)
        layout.addStretch()
        return frame

    def _build_content(self) -> QWidget:
        return self._build_instance_panel()

    def _build_instance_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel("Emulator instances")
        heading.setObjectName("metric")
        layout.addWidget(heading)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "#",
                "Platform",
                "Instance",
                "PID",
                "State",
                "Proxy status",
                "Proxy running",
                "Proxy IP",
                "Proxy",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
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
                self.proxy_checks.get(instance.index, ("Not checked", "—"))[0] if assigned else "—",
                self.proxy_checks.get(instance.index, ("Not checked", "—"))[1] if assigned else "—",
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
                if column == 6:
                    running_colors = {"Running": "#43cf78", "Not running": "#ef6c72", "Not checked": "#f0b84b"}
                    item.setForeground(QColor(running_colors.get(value, "#8f97ad")))
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

    def load_proxies_from_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load SOCKS5 proxies",
            str(Path.home()),
            "Text files (*.txt);;All files (*)",
        )
        if not file_name:
            return

        try:
            proxy_text = Path(file_name).read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Proxy file error", str(exc))
            return

        parsed_proxies, errors = parse_proxy_text(proxy_text, "socks5")
        proxies = []
        for proxy in parsed_proxies:
            if proxy.scheme != "socks5":
                errors.append(f"Skipped {proxy.display}: only SOCKS5 proxies are supported")
                continue
            proxies.append(proxy)
        self.proxies = proxies
        self.proxy_cursor = 0
        self.proxy_checks.clear()
        summary = f"SOCKS5 proxies: {len(proxies)}"
        if errors:
            summary += f" · Invalid: {len(errors)}"
            QMessageBox.warning(self, "Some proxies were skipped", "\n".join(errors[:10]))
        self._set_proxy_summary(summary)
        self._render_instances()
        self.statusBar().showMessage(summary, 5000)

    def clear_proxies(self) -> None:
        self.proxies.clear()
        self.assignments.clear()
        self.proxy_checks.clear()
        self.proxy_cursor = 0
        self._set_proxy_summary("SOCKS5 proxies: 0")
        self._render_instances()

    def assign_proxies(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return
        if not self.proxies:
            QMessageBox.information(self, "Load proxies", "Load a SOCKS5 proxy list before assigning proxies.")
            return

        mode = self.assignment_mode.currentData()
        if mode == "same":
            proxy = self.proxies[self.proxy_cursor % len(self.proxies)]
            for instance_index in indexes:
                self.assignments[instance_index] = proxy
                self.proxy_checks[instance_index] = self._check_proxy(proxy)
            self.proxy_cursor += 1
        else:
            for position, instance_index in enumerate(indexes):
                proxy_index = (self.proxy_cursor + position) % len(self.proxies)
                proxy = self.proxies[proxy_index]
                self.assignments[instance_index] = proxy
                self.proxy_checks[instance_index] = self._check_proxy(proxy)
            self.proxy_cursor += len(indexes)
        self._render_instances()
        self.statusBar().showMessage(f"Assigned proxies to {len(indexes)} instance(s)", 5000)

    def check_selected_proxies(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        checked = 0
        for instance_index in indexes:
            proxy = self.assignments.get(instance_index)
            if not proxy:
                continue
            self.proxy_checks[instance_index] = self._check_proxy(proxy)
            checked += 1
        self._render_instances()
        self.statusBar().showMessage(f"Checked {checked} assigned proxy/proxies", 5000)

    def _check_proxy(self, proxy: ProxyConfig) -> tuple[str, str]:
        try:
            proxy_ip = socket.gethostbyname(proxy.host)
        except OSError:
            proxy_ip = proxy.host

        try:
            with socket.create_connection((proxy.host, proxy.port), timeout=3):
                return "Running", proxy_ip
        except OSError:
            return "Not running", proxy_ip

    def _set_proxy_summary(self, text: str) -> None:
        if self.proxy_summary is not None:
            self.proxy_summary.setText(text)

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
