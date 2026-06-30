from __future__ import annotations

import json
import threading
from typing import Any, Optional
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.bot import BotManager
from app.emulators.base import EmulatorProvider
from app.features.stone_merge import StoneMergeScanner
from app.models import EmulatorInstance, InstanceState, WireGuardConfig
from app.wireguard import WireGuardEmulatorManager


class BackgroundSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.wireguard_source_file = Path.cwd() / ".wireguard_source.txt"
        self.wireguard_assignment_file = Path.cwd() / ".wireguard_assignments.json"
        self.instances: list[EmulatorInstance] = []
        self.wireguard_configs: list[WireGuardConfig] = []
        self.bot_manager = BotManager()
        self.wireguard_manager = WireGuardEmulatorManager(Path.cwd())
        self.stone_scanner = StoneMergeScanner(Path.cwd() / "assets" / "templates" / "stones")
        self.wireguard_summary: Optional[QLabel] = None
        self.bot_heading: Optional[QLabel] = None
        self.bot_metrics: list[QLabel] = []
        self.task_table: Optional[QTableWidget] = None
        self.background_tasks: list[BackgroundSignals] = []

        self.setWindowTitle("GrowStone Bot")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self.refresh_instances()
        self._load_wireguard_assignments()
        self._render_instances()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(6000)
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
        title = QLabel("GrowStone Bot")
        title.setObjectName("title")
        subtitle = QLabel("LDPlayer fleet control and per-instance WireGuard assignment")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch()

        self.total_metric = QLabel("Total: 0")
        self.running_metric = QLabel("Running: 0")
        self.assigned_metric = QLabel("Assigned: 0")
        for metric in (
            self.total_metric,
            self.running_metric,
            self.assigned_metric,
        ):
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
        assign = QPushButton("Assign .conf")
        assign.setObjectName("primary")
        assign.clicked.connect(self.assign_wireguard_config)
        setup = QPushButton("Install / import")
        setup.clicked.connect(self.install_or_import_wireguard)
        open_app = QPushButton("Open WireGuard")
        open_app.clicked.connect(self.open_wireguard_app)
        check = QPushButton("Check VPN IP")
        check.clicked.connect(self.check_selected_wireguard_ips)
        clear_configs = QPushButton("Clear configs")
        clear_configs.clicked.connect(self.clear_wireguard_configs)
        self.wireguard_summary = QLabel("WireGuard configs: 0")
        self.wireguard_summary.setObjectName("muted")

        for button in (select_all, clear, assign, setup, open_app, check, clear_configs):
            layout.addWidget(button)
        layout.addWidget(self.wireguard_summary)
        layout.addStretch()
        return frame

    def _build_content(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_instance_panel())
        splitter.addWidget(self._build_bot_panel())
        splitter.setSizes([620, 620])
        splitter.setChildrenCollapsible(False)
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
            [
                "Instance",
                "",
                "State",
                "Config",
                "WireGuard",
                "Public IP",
                "Ready",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.render_selected_instance_tasks)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 165)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        return frame

    def _build_bot_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.bot_heading = QLabel("Bot tasks")
        heading = self.bot_heading
        heading.setObjectName("metric")
        layout.addWidget(heading)

        metrics = QHBoxLayout()
        self.bot_metrics = []
        for text in ("Enabled: 0", "Idle: 0", "Errors: 0"):
            label = QLabel(text)
            label.setObjectName("metric")
            metrics.addWidget(label)
            self.bot_metrics.append(label)
        metrics.addStretch()
        layout.addLayout(metrics)

        self.task_table = QTableWidget(0, 3)
        self.task_table.setHorizontalHeaderLabels(["Task", "On", "Status"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.itemChanged.connect(self.update_task_state_from_table)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.task_table, 1)
        self.render_selected_instance_tasks()
        return frame

    def refresh_instances(self) -> None:
        try:
            fresh_instances = self.provider.list_instances()
        except Exception as exc:
            QMessageBox.critical(self, "LDPlayer error", str(exc))
            return

        self.instances = fresh_instances
        self.bot_manager.sync_instances(self.instances)
        for instance in self.instances:
            assigned = self.bot_manager.person(instance.index).wireguard_config
            instance.network = assigned.display if assigned else None
        self._render_instances()
        self.statusBar().showMessage(self.provider.display_name)

    def _render_instances(self) -> None:
        self.table.setRowCount(len(self.instances))
        for row, instance in enumerate(self.instances):
            person = self.bot_manager.person(instance.index)
            assigned = person.wireguard_config
            check = person.wireguard_check
            ready = "Ready" if assigned and check and check[0] == "IP OK" else "Assigned" if assigned else "Off"
            values = (
                instance.name,
                "",
                instance.state.value,
                assigned.display if assigned else "Unassigned",
                check[0] if assigned and check else "Not checked" if assigned else "-",
                check[1] if assigned and check else "-",
                ready,
            )
            for column, value in enumerate(values):
                if column == 1:
                    self.table.setCellWidget(row, column, self._build_row_actions(instance.index))
                    continue
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, instance.index)
                if column == 0:
                    item.setForeground(QColor("#172033"))
                if column == 2:
                    state_colors = {
                        InstanceState.RUNNING: "#198754",
                        InstanceState.STARTING: "#b7791f",
                        InstanceState.STOPPED: "#69758a",
                        InstanceState.UNKNOWN: "#d64550",
                    }
                    color = state_colors[instance.state]
                    item.setForeground(QColor(color))
                if column == 3:
                    item.setForeground(QColor("#4169e1" if assigned else "#69758a"))
                if column == 4:
                    running_colors = {
                        "Running": "#198754",
                        "Tunnel": "#198754",
                        "Routed": "#198754",
                        "Bridge OK": "#198754",
                        "Installed": "#198754",
                        "IP OK": "#198754",
                        "Not running": "#d64550",
                        "Auth failed": "#d64550",
                        "Not checked": "#b7791f",
                        "IP check failed": "#b7791f",
                    }
                    item.setForeground(QColor(running_colors.get(value, "#69758a")))
                if column == 6:
                    item.setForeground(QColor("#198754" if ready == "Ready" else "#69758a"))
                self.table.setItem(row, column, item)

        running = sum(item.state == InstanceState.RUNNING for item in self.instances)
        self.total_metric.setText(f"Total: {len(self.instances)}")
        self.running_metric.setText(f"Running: {running}")
        self.assigned_metric.setText(f"Assigned: {self.bot_manager.assigned_count()}")

    def _build_row_actions(self, instance_index: int) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        actions = (
            (
                QStyle.SP_MediaPlay,
                "Start",
                lambda: self._run_instance_action(instance_index, self.provider.start, "started"),
            ),
            (
                QStyle.SP_MediaStop,
                "Stop",
                lambda: self._run_instance_action(instance_index, self.provider.stop, "stopped"),
            ),
            (
                QStyle.SP_BrowserReload,
                "Restart",
                lambda: self._run_instance_action(instance_index, self.provider.restart, "restarted"),
            ),
        )
        for icon_name, tooltip, callback in actions:
            button = QPushButton()
            button.setObjectName("iconButton")
            button.setIcon(self.style().standardIcon(icon_name))
            button.setToolTip(tooltip)
            button.setFixedSize(28, 28)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch()
        return widget

    def render_selected_instance_tasks(self) -> None:
        if self.task_table is None:
            return

        indexes = self.selected_indexes()
        if not indexes:
            self._render_task_table(None)
            return
        self._render_task_table(indexes[0])

    def update_task_state_from_table(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        instance_index = self._task_panel_instance_index()
        if instance_index is None:
            return
        tasks = self.bot_manager.person(instance_index).tasks or []
        if item.row() >= len(tasks):
            return
        enabled = item.checkState() == Qt.Checked
        person = self.bot_manager.person(instance_index)
        if enabled and person.wireguard_config is None:
            QMessageBox.information(
                self,
                "Assign WireGuard first",
                f"Assign a WireGuard .conf to instance {instance_index} before enabling bot tasks.",
            )
            self._render_task_table(instance_index)
            return
        if enabled and (person.wireguard_check is None or person.wireguard_check[0] != "IP OK"):
            self._start_task_after_wireguard_check(instance_index, item.row())
            self._render_task_table(instance_index)
            return
        person.set_task_enabled(item.row(), enabled)
        self._render_task_table(instance_index)
        if enabled:
            self._run_enabled_task_once(instance_index, item.row())

    def _render_task_table(self, instance_index: Optional[int]) -> None:
        if self.task_table is None:
            return

        self.task_table.blockSignals(True)
        if instance_index is None:
            self.task_table.setRowCount(0)
            if self.bot_heading is not None:
                self.bot_heading.setText("Bot tasks")
            self._set_bot_metrics(0, 0, 0)
            self.task_table.blockSignals(False)
            return

        instance = self._instance_by_index(instance_index)
        title = instance.name if instance is not None else f"Instance {instance_index}"
        if self.bot_heading is not None:
            self.bot_heading.setText(f"Bot tasks - {title}")

        person = self.bot_manager.person(instance_index)
        tasks = person.tasks or []
        self.task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            task_item = QTableWidgetItem(task.name)
            enabled_item = QTableWidgetItem()
            flags = Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            if person.wireguard_config is not None:
                flags |= Qt.ItemIsEnabled
            enabled_item.setFlags(flags)
            enabled_item.setCheckState(Qt.Checked if task.enabled else Qt.Unchecked)
            status_item = QTableWidgetItem(task.status)
            status_item.setForeground(QColor("#198754" if task.status == "Idle" else "#69758a"))
            self.task_table.setItem(row, 0, task_item)
            self.task_table.setItem(row, 1, enabled_item)
            self.task_table.setItem(row, 2, status_item)
        enabled_count, idle_count, error_count = person.task_counts()
        self._set_bot_metrics(enabled_count, idle_count, error_count)
        self.task_table.blockSignals(False)

    def _task_panel_instance_index(self) -> Optional[int]:
        indexes = self.selected_indexes()
        return indexes[0] if indexes else None

    def _set_bot_metrics(self, enabled: int, idle: int, errors: int) -> None:
        values = (f"Enabled: {enabled}", f"Idle: {idle}", f"Errors: {errors}")
        for label, value in zip(self.bot_metrics, values):
            label.setText(value)

    def _start_task_after_wireguard_check(self, instance_index: int, task_row: int) -> None:
        instance = self._instance_by_index(instance_index)
        if instance is None or not instance.live_pids():
            QMessageBox.information(
                self,
                "Start LDPlayer first",
                f"Start instance {instance_index} before enabling bot tasks.",
            )
            return

        self.statusBar().showMessage(f"Checking WireGuard IP for instance {instance_index}...", 5000)

        def work() -> dict[str, Any]:
            return {
                "instance_index": instance_index,
                "task_row": task_row,
                "routed_result": self._apply_saved_wireguard_check_blocking(instance_index),
            }

        self._run_background(
            work,
            self._finish_start_task_after_wireguard_check,
            "Bot start failed",
        )

    def _finish_start_task_after_wireguard_check(self, result: object) -> None:
        if not isinstance(result, dict):
            self.refresh_instances()
            return

        instance_index = int(result["instance_index"])
        task_row = int(result["task_row"])
        routed_result = result.get("routed_result")

        if isinstance(routed_result, dict):
            warning = routed_result.get("warning")
            if warning:
                self.refresh_instances()
                QMessageBox.warning(self, str(routed_result.get("title", "WireGuard check failed")), str(warning))
                return

        self.bot_manager.person(instance_index).set_task_enabled(task_row, True)
        self.refresh_instances()
        self.render_selected_instance_tasks()

        message = "Bot task enabled"
        if isinstance(routed_result, dict) and routed_result.get("message"):
            message = str(routed_result["message"])
        self.statusBar().showMessage(message, 5000)
        self._run_enabled_task_once(instance_index, task_row)

    def table_select_all(self) -> None:
        self.table.selectAll()

    def clear_selection(self) -> None:
        self.table.clearSelection()

    def selected_indexes(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.instances[row].index for row in rows]

    def assign_wireguard_config(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        config_path = self._choose_wireguard_config_file()
        if config_path is None:
            return

        config = WireGuardConfig(str(config_path.resolve()))
        for instance_index in indexes:
            self.bot_manager.assign_wireguard(instance_index, config)
        self._save_wireguard_config_file(config_path)
        self._save_wireguard_assignments()
        self._set_wireguard_summary(f"WireGuard configs: {self.bot_manager.assigned_count()}")
        self._render_instances()
        self.statusBar().showMessage(f"Assigned {config.display} to {len(indexes)} instance(s)", 5000)

    def install_or_import_wireguard(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        missing = [index for index in indexes if self.bot_manager.person(index).wireguard_config is None]
        if missing:
            QMessageBox.information(
                self,
                "Assign config first",
                "Assign a WireGuard .conf before installing/importing it.",
            )
            return

        self.statusBar().showMessage("Installing/importing WireGuard config...", 5000)

        def work() -> dict[str, object]:
            results: list[str] = []
            for instance_index in indexes:
                config = self.bot_manager.person(instance_index).wireguard_config
                if config is None:
                    continue
                result = self.wireguard_manager.ensure_installed_and_imported(
                    instance_index,
                    Path(config.file_path),
                )
                self.bot_manager.person(instance_index).wireguard_check = ("Installed", "Pending IP check")
                import_note = "import opened" if result.import_started else "open WireGuard and import from Downloads"
                results.append(f"Instance {instance_index}: {import_note}")
            return {"results": results}

        self._run_background(work, self._finish_wireguard_setup, "WireGuard setup failed")

    def _finish_wireguard_setup(self, result: object) -> None:
        self.refresh_instances()
        results = []
        if isinstance(result, dict):
            raw_results = result.get("results")
            if isinstance(raw_results, list):
                results = [str(item) for item in raw_results]
        if results:
            QMessageBox.information(self, "WireGuard setup", "\n".join(results[:12]))
        self.statusBar().showMessage("WireGuard setup finished", 5000)

    def open_wireguard_app(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        failures: list[str] = []
        for instance_index in indexes:
            try:
                if not self.wireguard_manager.is_installed(instance_index):
                    config = self.bot_manager.person(instance_index).wireguard_config
                    if config is None:
                        raise RuntimeError("assign a WireGuard .conf first")
                    self.wireguard_manager.ensure_installed_and_imported(instance_index, Path(config.file_path))
                self.wireguard_manager.open_app(instance_index)
            except Exception as exc:
                failures.append(f"Instance {instance_index}: {exc}")
        if failures:
            QMessageBox.warning(self, "Open WireGuard failed", "\n".join(failures[:10]))
        else:
            self.statusBar().showMessage(f"Opened WireGuard on {len(indexes)} instance(s)", 5000)

    def check_selected_wireguard_ips(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        self.statusBar().showMessage("Checking public IP inside selected emulator(s)...", 5000)

        def work() -> dict[str, object]:
            results: list[str] = []
            failures: list[str] = []
            for instance_index in indexes:
                try:
                    public_ip = self.wireguard_manager.public_ip(instance_index)
                    self.bot_manager.person(instance_index).wireguard_check = ("IP OK", public_ip)
                    results.append(f"Instance {instance_index}: {public_ip}")
                except Exception as exc:
                    self.bot_manager.person(instance_index).wireguard_check = ("IP check failed", str(exc))
                    failures.append(f"Instance {instance_index}: {exc}")
            return {"results": results, "failures": failures}

        self._run_background(work, self._finish_wireguard_ip_check, "WireGuard IP check failed")

    def _finish_wireguard_ip_check(self, result: object) -> None:
        self.refresh_instances()
        results: list[str] = []
        failures: list[str] = []
        if isinstance(result, dict):
            raw_results = result.get("results")
            raw_failures = result.get("failures")
            if isinstance(raw_results, list):
                results = [str(item) for item in raw_results]
            if isinstance(raw_failures, list):
                failures = [str(item) for item in raw_failures]
        if failures:
            QMessageBox.warning(self, "Some IP checks failed", "\n".join(failures[:10]))
        if results:
            self.statusBar().showMessage("; ".join(results[:3]), 8000)

    def clear_wireguard_configs(self) -> None:
        indexes = self.selected_indexes()
        target_indexes = indexes or list(self.bot_manager.people)
        for instance_index in target_indexes:
            self.bot_manager.person(instance_index).clear_wireguard()
        self._save_wireguard_assignments()
        self._set_wireguard_summary(f"WireGuard configs: {self.bot_manager.assigned_count()}")
        self._render_instances()
        self.statusBar().showMessage(f"Cleared WireGuard config(s) for {len(target_indexes)} instance(s)", 5000)

    def _choose_wireguard_config_file(self) -> Optional[Path]:
        last_config = self._saved_wireguard_config_file()
        start_dir = last_config.parent if last_config is not None else self._default_wireguard_dir()
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Assign WireGuard config",
            str(start_dir),
            "WireGuard configs (*.conf);;All files (*)",
        )
        return Path(file_name) if file_name else None

    def _default_wireguard_dir(self) -> Path:
        work_dir = Path.cwd().parent / "work"
        if work_dir.is_dir():
            return work_dir
        downloads = Path.home() / "Downloads"
        return downloads if downloads.is_dir() else Path.home()

    def _saved_wireguard_config_file(self) -> Optional[Path]:
        try:
            value = self.wireguard_source_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        path = Path(value)
        return path if path.is_file() and path.suffix.lower() == ".conf" else None

    def _save_wireguard_config_file(self, config_path: Path) -> None:
        try:
            self.wireguard_source_file.write_text(str(config_path.resolve()), encoding="utf-8")
        except OSError:
            pass

    def _load_wireguard_assignments(self) -> None:
        try:
            raw_assignments = json.loads(self.wireguard_assignment_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "WireGuard assignment error", f"Could not load saved assignments: {exc}")
            return
        if not isinstance(raw_assignments, dict):
            QMessageBox.warning(self, "WireGuard assignment error", "Saved WireGuard assignments must be a JSON object.")
            return

        loaded = 0
        errors: list[str] = []
        for instance_key, config_value in raw_assignments.items():
            instance_index = self._instance_index_for_assignment_key(instance_key)
            if instance_index is None:
                errors.append(f"{instance_key}: instance not found")
                continue
            if not isinstance(config_value, str):
                errors.append(f"Instance {instance_index}: config path must be text")
                continue
            config_path = Path(config_value)
            if not config_path.is_file():
                errors.append(f"Instance {instance_index}: config file was not found")
                continue
            self.bot_manager.assign_wireguard(instance_index, WireGuardConfig(str(config_path.resolve())))
            loaded += 1

        if loaded:
            self._set_wireguard_summary(f"WireGuard configs: {loaded}")
            self.statusBar().showMessage(f"Loaded {loaded} saved WireGuard assignment(s)", 5000)
        if errors:
            QMessageBox.warning(self, "Some saved WireGuard assignments were skipped", "\n".join(errors[:10]))

    def _save_wireguard_assignments(self) -> None:
        assignments = {}
        for instance_index, person in sorted(self.bot_manager.people.items()):
            config = person.wireguard_config
            if config is None:
                continue
            key = self._assignment_key(instance_index)
            if key is not None:
                assignments[key] = config.file_path
        try:
            if assignments:
                self.wireguard_assignment_file.write_text(
                    json.dumps(assignments, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            elif self.wireguard_assignment_file.exists():
                self.wireguard_assignment_file.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "WireGuard assignment error", f"Could not save assignments: {exc}")

    def _assignment_key(self, instance_index: int) -> Optional[str]:
        return str(instance_index)

    def _instance_index_for_assignment_key(self, key: object) -> Optional[int]:
        if isinstance(key, str):
            try:
                return int(key)
            except ValueError:
                if key.startswith("ldplayer:"):
                    try:
                        return int(key.rsplit(":", 1)[1])
                    except (IndexError, ValueError):
                        return None
                return None
        if isinstance(key, int):
            return key
        return None

    def _instance_by_index(self, instance_index: int) -> Optional[EmulatorInstance]:
        return next((instance for instance in self.instances if instance.index == instance_index), None)

    def _set_wireguard_summary(self, text: str) -> None:
        if self.wireguard_summary is not None:
            self.wireguard_summary.setText(text)

    def _run_instance_action(
        self,
        instance_index: int,
        action: Callable[[int], None],
        verb: str,
    ) -> None:
        self.statusBar().showMessage(f"Instance {instance_index} {verb}...", 5000)
        self.refresh_timer.stop()

        def work() -> dict[str, Any]:
            action(instance_index)
            return {
                "instance_index": instance_index,
                "verb": verb,
            }

        self._run_background(
            work,
            self._finish_instance_action,
            "Instance action failed",
        )

    def _finish_instance_action(self, result: object) -> None:
        self.refresh_timer.start()
        if not isinstance(result, dict):
            self.refresh_instances()
            return

        instance_index = int(result["instance_index"])
        verb = str(result["verb"])
        self.refresh_instances()

        self.statusBar().showMessage(f"Instance {instance_index} {verb}", 5000)

    def _run_background(
        self,
        work: Callable[[], object],
        on_finished: Callable[[object], None],
        error_title: str,
    ) -> None:
        signals = BackgroundSignals()
        self.background_tasks.append(signals)

        def cleanup() -> None:
            if signals in self.background_tasks:
                self.background_tasks.remove(signals)
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()

        def finish(result: object) -> None:
            cleanup()
            on_finished(result)

        def fail(message: str) -> None:
            cleanup()
            QMessageBox.warning(self, error_title, message)
            self.refresh_instances()

        signals.finished.connect(finish)
        signals.failed.connect(fail)

        def runner() -> None:
            try:
                signals.finished.emit(work())
            except Exception as exc:
                signals.failed.emit(str(exc))

        threading.Thread(target=runner, name="ui-background-task", daemon=True).start()

    def _run_enabled_task_once(self, instance_index: int, task_row: int) -> None:
        person = self.bot_manager.person(instance_index)
        tasks = person.tasks or []
        if task_row >= len(tasks) or tasks[task_row].name != "Merge stones":
            return

        task = tasks[task_row]
        task.status = "Scanning"
        self._render_task_table(instance_index)
        self.statusBar().showMessage(f"Scanning stones for instance {instance_index}...", 5000)

        def work() -> dict[str, object]:
            screenshot = self.provider.screenshot_png(instance_index)
            candidate = self.stone_scanner.find_merge_candidate(screenshot)
            if candidate is None:
                return {"instance_index": instance_index, "task_row": task_row, "merged": False}
            self.provider.drag(instance_index, candidate.drag_from, candidate.drag_to)
            return {
                "instance_index": instance_index,
                "task_row": task_row,
                "merged": True,
                "template": candidate.template_name,
                "from": candidate.drag_from,
                "to": candidate.drag_to,
            }

        self._run_background(
            work,
            self._finish_merge_stones_once,
            "Merge stones failed",
        )

    def _finish_merge_stones_once(self, result: object) -> None:
        if not isinstance(result, dict):
            self.refresh_instances()
            return
        instance_index = int(result["instance_index"])
        task_row = int(result["task_row"])
        person = self.bot_manager.person(instance_index)
        tasks = person.tasks or []
        if task_row >= len(tasks):
            return
        if result.get("merged"):
            start = result.get("from")
            end = result.get("to")
            tasks[task_row].status = "Merged"
            self.statusBar().showMessage(
                f"Merged {result.get('template')} on instance {instance_index}: {start} -> {end}",
                5000,
            )
        else:
            tasks[task_row].status = "No match"
            self.statusBar().showMessage(f"No matching stones found on instance {instance_index}", 5000)
        self._render_task_table(instance_index)

    def _apply_saved_wireguard_check_blocking(self, instance_index: int) -> Optional[dict[str, str]]:
        person = self.bot_manager.person(instance_index)
        config = person.wireguard_config
        if config is None:
            return {
                "title": "WireGuard config missing",
                "warning": f"Instance {instance_index}: assign a WireGuard .conf first.",
            }

        try:
            self.wireguard_manager.ensure_installed_and_imported(instance_index, Path(config.file_path))
            public_ip = self.wireguard_manager.public_ip(instance_index)
        except Exception as exc:
            person.wireguard_check = ("IP check failed", str(exc))
            return {
                "title": "WireGuard check failed",
                "warning": f"Instance {instance_index}: {exc}",
            }

        person.wireguard_check = ("IP OK", public_ip)
        return {
            "message": f"WireGuard IP check for instance {instance_index}: {public_ip}"
        }

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
