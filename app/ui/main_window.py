from __future__ import annotations

import json
import random
import threading
import time
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
from app.paths import APP_ROOT, OUTPUT_DIR, STONE_TEMPLATE_DIR
from app.wireguard import WireGuardEmulatorManager


STONE_MERGE_INTERVAL_SECONDS = (35, 45)
STONE_MERGE_SETTLE_SECONDS = 0.65
STONE_MERGE_DRAG_DURATION_MS = (50, 100)
STONE_TEMPLATE_CHECK_COLUMN = 0
STONE_TEMPLATE_NAME_COLUMN = 1


class BackgroundSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.app_root = APP_ROOT
        self.wireguard_source_file = self.app_root / ".wireguard_source.txt"
        self.wireguard_assignment_file = self.app_root / ".wireguard_assignments.json"
        self.stone_template_settings_file = self.app_root / ".stone_templates.json"
        self.instances: list[EmulatorInstance] = []
        self.wireguard_configs: list[WireGuardConfig] = []
        self.bot_manager = BotManager()
        self.wireguard_manager = WireGuardEmulatorManager(self.app_root)
        self.stone_scanner = StoneMergeScanner(STONE_TEMPLATE_DIR)
        self.stone_template_enabled: dict[str, bool] = {}
        self.wireguard_summary: Optional[QLabel] = None
        self.bot_heading: Optional[QLabel] = None
        self.bot_metrics: list[QLabel] = []
        self.task_table: Optional[QTableWidget] = None
        self.stone_template_table: Optional[QTableWidget] = None
        self.background_tasks: list[BackgroundSignals] = []
        self.running_task_ticks: set[tuple[int, int]] = set()
        self.scheduled_task_ticks: set[tuple[int, int]] = set()

        self.setWindowTitle("GrowStone Bot")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self._load_stone_template_settings()
        self.refresh_stone_templates()
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

        stone_actions = QHBoxLayout()
        stone_heading = QLabel("Stone templates")
        stone_heading.setObjectName("metric")
        refresh_templates = QPushButton("Refresh")
        refresh_templates.clicked.connect(self.refresh_stone_templates)
        preview_area = QPushButton("Preview bag area")
        preview_area.clicked.connect(self.preview_stone_bag_area)
        stone_actions.addWidget(stone_heading)
        stone_actions.addStretch()
        stone_actions.addWidget(refresh_templates)
        stone_actions.addWidget(preview_area)
        layout.addLayout(stone_actions)

        self.stone_template_table = QTableWidget(0, 2)
        self.stone_template_table.setHorizontalHeaderLabels(["Merge", "Template"])
        self.stone_template_table.verticalHeader().setVisible(False)
        self.stone_template_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stone_template_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stone_template_table.setAlternatingRowColors(True)
        self.stone_template_table.itemChanged.connect(self.update_stone_template_state)
        self.stone_template_table.horizontalHeader().setSectionResizeMode(STONE_TEMPLATE_CHECK_COLUMN, QHeaderView.ResizeToContents)
        self.stone_template_table.horizontalHeader().setSectionResizeMode(STONE_TEMPLATE_NAME_COLUMN, QHeaderView.Stretch)
        self.stone_template_table.setMaximumHeight(160)
        layout.addWidget(self.stone_template_table)

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
        person.set_task_enabled(item.row(), enabled)
        self._append_merge_log(instance_index, f"task row {item.row()} {'enabled' if enabled else 'disabled'}")
        self._render_task_table(instance_index)
        if enabled:
            self._append_merge_log(instance_index, f"task row {item.row()} start requested")
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
            enabled_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            enabled_item.setCheckState(Qt.Checked if task.enabled else Qt.Unchecked)
            status_item = QTableWidgetItem("On" if task.enabled else "Off")
            status_item.setForeground(QColor("#198754" if task.enabled else "#69758a"))
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

    def refresh_stone_templates(self) -> None:
        names = self.stone_scanner.template_names()
        for name in names:
            self.stone_template_enabled.setdefault(name, True)
        self._apply_stone_template_filter()
        self._render_stone_template_table(names)

    def update_stone_template_state(self, item: QTableWidgetItem) -> None:
        if item.column() != STONE_TEMPLATE_CHECK_COLUMN:
            return
        name_item = self.stone_template_table.item(item.row(), STONE_TEMPLATE_NAME_COLUMN) if self.stone_template_table else None
        if name_item is None:
            return
        self.stone_template_enabled[name_item.text()] = item.checkState() == Qt.Checked
        self._apply_stone_template_filter()
        self._save_stone_template_settings()

    def preview_stone_bag_area(self) -> None:
        instance_index = self._task_panel_instance_index()
        if instance_index is None:
            QMessageBox.information(self, "Select instance", "Select one running emulator instance first.")
            return
        instance = self._instance_by_index(instance_index)
        if instance is None or not instance.live_pids():
            QMessageBox.information(self, "Start LDPlayer first", f"Start instance {instance_index} before previewing.")
            return

        self.statusBar().showMessage(f"Capturing bag area preview for instance {instance_index}...", 5000)

        def work() -> dict[str, object]:
            screenshot = self.provider.screenshot_png(instance_index)
            output_path = (
                OUTPUT_DIR
                / "stone-debug"
                / f"instance-{instance_index}-bag-area-{int(time.time())}.png"
            )
            preview = self.stone_scanner.write_debug_overlay(screenshot, output_path)
            return {
                "path": str(preview.path),
                "match_count": preview.match_count,
                "template_count": preview.template_count,
                "uncertain_count": preview.uncertain_count,
                "slot_count": preview.slot_count,
            }

        self._run_background(work, self._finish_preview_stone_bag_area, "Bag preview failed")

    def _finish_preview_stone_bag_area(self, result: object) -> None:
        if not isinstance(result, dict) or not result.get("path"):
            return
        path = str(result["path"])
        match_count = int(result.get("match_count", 0))
        template_count = int(result.get("template_count", 0))
        uncertain_count = int(result.get("uncertain_count", 0))
        slot_count = int(result.get("slot_count", 0))
        QMessageBox.information(
            self,
            "Bag area preview",
            f"Saved preview image:\n{path}\n\n"
            f"Confident slots: {match_count}/{slot_count}\n"
            f"Uncertain slots: {uncertain_count}\n"
            f"Templates loaded: {template_count}",
        )
        self.statusBar().showMessage(
            f"Saved bag preview: {match_count}/{slot_count} confident, {uncertain_count} uncertain",
            8000,
        )

    def _render_stone_template_table(self, names: list[str]) -> None:
        if self.stone_template_table is None:
            return
        self.stone_template_table.blockSignals(True)
        self.stone_template_table.setRowCount(len(names))
        for row, name in enumerate(names):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            enabled_item.setCheckState(Qt.Checked if self.stone_template_enabled.get(name, True) else Qt.Unchecked)
            name_item = QTableWidgetItem(name)
            self.stone_template_table.setItem(row, STONE_TEMPLATE_CHECK_COLUMN, enabled_item)
            self.stone_template_table.setItem(row, STONE_TEMPLATE_NAME_COLUMN, name_item)
        self.stone_template_table.blockSignals(False)

    def _apply_stone_template_filter(self) -> None:
        enabled = {name for name, is_enabled in self.stone_template_enabled.items() if is_enabled}
        self.stone_scanner.set_enabled_templates(enabled)

    def _load_stone_template_settings(self) -> None:
        try:
            raw_settings = json.loads(self.stone_template_settings_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Stone template settings", f"Could not load saved stone template settings: {exc}")
            return
        if not isinstance(raw_settings, dict):
            return
        for name, enabled in raw_settings.items():
            if isinstance(name, str) and isinstance(enabled, bool):
                self.stone_template_enabled[name] = enabled

    def _save_stone_template_settings(self) -> None:
        try:
            self.stone_template_settings_file.write_text(
                json.dumps(self.stone_template_enabled, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "Stone template settings", f"Could not save stone template settings: {exc}")

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

    def assigned_wireguard_indexes(self) -> list[int]:
        return [
            instance.index
            for instance in self.instances
            if self.bot_manager.person(instance.index).wireguard_config is not None
        ]

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
        selected_indexes = self.selected_indexes()
        indexes = selected_indexes or self.assigned_wireguard_indexes()
        if not indexes:
            QMessageBox.information(
                self,
                "Assign config first",
                "Assign a WireGuard .conf to at least one emulator before installing/importing.",
            )
            return

        missing = [index for index in indexes if self.bot_manager.person(index).wireguard_config is None]
        if missing:
            QMessageBox.information(
                self,
                "Assign config first",
                "Some selected emulator(s) do not have a WireGuard .conf assigned.",
            )
            return

        scope = "selected emulator(s)" if selected_indexes else "assigned emulator(s)"
        self.statusBar().showMessage(f"Installing/importing WireGuard config for {scope}...", 5000)

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
        work_dir = self.app_root.parent / "work"
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
            QMessageBox.warning(self, error_title, _friendly_error_message(message))
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
        self._append_merge_log(instance_index, f"task row {task_row} runner entered")
        tick_key = (instance_index, task_row)
        if tick_key in self.running_task_ticks:
            self._append_merge_log(instance_index, f"task row {task_row} skipped: already running")
            return
        person = self.bot_manager.person(instance_index)
        tasks = person.tasks or []
        if task_row >= len(tasks) or tasks[task_row].name != "Merge stones":
            self._append_merge_log(instance_index, f"task row {task_row} skipped: not merge task")
            return
        if not tasks[task_row].enabled:
            self._append_merge_log(instance_index, f"task row {task_row} skipped: disabled")
            return

        self.refresh_stone_templates()
        self._render_task_table(instance_index)
        self.running_task_ticks.add(tick_key)

        def work() -> dict[str, object]:
            try:
                self._append_merge_log(instance_index, "tick started")
                screenshot = self.provider.screenshot_png(instance_index)
                matches = self.stone_scanner.find_confident_matches(screenshot)
                counts: dict[str, int] = {}
                for match in matches:
                    counts[match.template_name] = counts.get(match.template_name, 0) + 1
                candidates = self.stone_scanner.merge_candidates_for_matches(matches)
                self._append_merge_log(
                    instance_index,
                    f"confident matches {len(matches)} [{_format_counts(counts)}], candidates {len(candidates)}",
                )
                if not candidates:
                    return {
                        "instance_index": instance_index,
                        "task_row": task_row,
                        "merged_count": 0,
                    }
                merges = []
                for candidate in candidates:
                    duration_ms = random.randint(*STONE_MERGE_DRAG_DURATION_MS)
                    self._append_merge_log(
                        instance_index,
                        f"drag {candidate.template_name} {candidate.drag_from}->{candidate.drag_to} {duration_ms}ms",
                    )
                    self.provider.drag(instance_index, candidate.drag_from, candidate.drag_to, duration_ms=duration_ms)
                    merges.append(
                        {
                            "template": candidate.template_name,
                            "from": candidate.drag_from,
                            "to": candidate.drag_to,
                            "duration_ms": duration_ms,
                        }
                    )
                    if STONE_MERGE_SETTLE_SECONDS > 0:
                        time.sleep(STONE_MERGE_SETTLE_SECONDS)
                return {
                    "instance_index": instance_index,
                    "task_row": task_row,
                    "merged_count": len(merges),
                    "merges": merges,
                }
            except Exception as exc:
                self._append_merge_log(instance_index, f"error: {exc}")
                return {
                    "instance_index": instance_index,
                    "task_row": task_row,
                    "error": str(exc),
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
        error = result.get("error")
        self.running_task_ticks.discard((instance_index, task_row))
        if error:
            tasks[task_row].status = "Error"
            QMessageBox.warning(self, "Merge stones", str(error))
            self.statusBar().showMessage(f"Merge stones failed on instance {instance_index}", 5000)
        elif int(result.get("merged_count", 0)):
            self.statusBar().showMessage(f"Merged {int(result.get('merged_count', 0))} pair(s) on instance {instance_index}", 5000)
        self._render_task_table(instance_index)
        if not error and tasks[task_row].enabled:
            self._schedule_enabled_task_tick(instance_index, task_row)

    def _schedule_enabled_task_tick(self, instance_index: int, task_row: int) -> None:
        tick_key = (instance_index, task_row)
        if tick_key in self.running_task_ticks or tick_key in self.scheduled_task_ticks:
            return
        self.scheduled_task_ticks.add(tick_key)

        def run_tick() -> None:
            self.scheduled_task_ticks.discard(tick_key)
            person = self.bot_manager.person(instance_index)
            tasks = person.tasks or []
            if task_row >= len(tasks) or not tasks[task_row].enabled:
                return
            self._run_enabled_task_once(instance_index, task_row)

        delay_seconds = random.randint(*STONE_MERGE_INTERVAL_SECONDS)
        self._append_merge_log(instance_index, f"next tick in {delay_seconds}s")
        QTimer.singleShot(delay_seconds * 1000, run_tick)

    def _append_merge_log(self, instance_index: int, message: str) -> None:
        try:
            log_dir = OUTPUT_DIR / "stone-debug"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with (log_dir / "merge-events.log").open("a", encoding="utf-8") as log_file:
                log_file.write(f"{timestamp} instance {instance_index}: {message}\n")
        except OSError:
            pass

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


def _friendly_error_message(message: str) -> str:
    if "\ufffdPNG" in message or "%PNG" in message or len(message) > 1200:
        return "The emulator returned screenshot data as an error. Try the action again after ADB reconnects."
    return message


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
