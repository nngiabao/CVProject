from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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
from app.process_utils import ldplayer_related_pids
from app.proxy_check import check_proxy
from app.proxy_parser import parse_proxy_text
from app.routing import RoutingService
from app.windivert_guard import WinDivertGuard
from app.windivert_support import WinDivertStatus, check_windivert


class MainWindow(QMainWindow):
    def __init__(self, provider: EmulatorProvider) -> None:
        super().__init__()
        self.provider = provider
        self.proxy_source_file = Path.cwd() / ".proxy_source.txt"
        self.instances: list[EmulatorInstance] = []
        self.proxies: list[ProxyConfig] = []
        self.assignments: dict[int, ProxyConfig] = {}
        self.proxy_cursor = 0
        self.proxy_checks: dict[int, tuple[str, str]] = {}
        self.routing = RoutingService()
        self.windivert_guard = WinDivertGuard()
        self.windivert_status: WinDivertStatus = check_windivert()
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
        self.protection_metric = QLabel(self._protection_label())
        self.guard_metric = QLabel("Guard: 0 PIDs / 0 blocked")
        for metric in (
            self.total_metric,
            self.running_metric,
            self.assigned_metric,
            self.protection_metric,
            self.guard_metric,
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
        start = QPushButton("Start")
        start.setObjectName("success")
        start.clicked.connect(lambda: self._run_selected(self.provider.start, "started"))
        stop = QPushButton("Stop")
        stop.setObjectName("danger")
        stop.clicked.connect(lambda: self._run_selected(self.provider.stop, "stopped"))
        restart = QPushButton("Restart")
        restart.clicked.connect(lambda: self._run_selected(self.provider.restart, "restarted"))
        route = QPushButton("Start proxy routing")
        route.setToolTip("Starts local authenticated SOCKS5 bridges for selected assigned instances")
        route.clicked.connect(self.start_proxy_routing)
        stop_route = QPushButton("Stop proxy routing")
        stop_route.clicked.connect(self.stop_proxy_routing)

        for button in (select_all, clear, load, clear_proxies, assign, check):
            layout.addWidget(button)
        layout.addWidget(self.proxy_summary)
        for button in (start, stop, restart, route, stop_route):
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
                "Routing",
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
        self._update_windivert_guard()
        self._render_instances()
        self.statusBar().showMessage(self.provider.display_name)

    def _render_instances(self) -> None:
        self.table.setRowCount(len(self.instances))
        for row, instance in enumerate(self.instances):
            assigned = self.assignments.get(instance.index)
            route = self.routing.session(instance.index)
            values = (
                str(instance.index),
                instance.platform,
                instance.name,
                str(instance.pid or "—"),
                instance.state.value,
                "Assigned" if assigned else "Unassigned",
                self.proxy_checks.get(instance.index, ("Not checked", "—"))[0] if assigned else "—",
                self.proxy_checks.get(instance.index, ("Not checked", "—"))[1] if assigned else "—",
                route.local_proxy if route else "Off",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, instance.index)
                if column == 2:
                    item.setForeground(QColor("#ffffff"))
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
                    running_colors = {
                        "Running": "#43cf78",
                        "Not running": "#ef6c72",
                        "Auth failed": "#ef6c72",
                        "Not checked": "#f0b84b",
                    }
                    item.setForeground(QColor(running_colors.get(value, "#8f97ad")))
                if column == 8:
                    item.setForeground(QColor("#43cf78" if route else "#8f97ad"))
                self.table.setItem(row, column, item)

        running = sum(item.state == InstanceState.RUNNING for item in self.instances)
        self.total_metric.setText(f"Total: {len(self.instances)}")
        self.running_metric.setText(f"Running: {running}")
        self.assigned_metric.setText(f"Assigned: {len(self.assignments)}")
        self.protection_metric.setText(self._protection_label())
        guard_stats = self.windivert_guard.stats
        self.guard_metric.setText(f"Guard: {guard_stats.protected_pids} PIDs / {guard_stats.blocked} blocked")

    def table_select_all(self) -> None:
        self.table.selectAll()

    def clear_selection(self) -> None:
        self.table.clearSelection()

    def selected_indexes(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.instances[row].index for row in rows]

    def load_proxies_from_file(self) -> None:
        proxy_file = self._resolve_proxy_file()
        if proxy_file is None:
            return

        self._load_proxy_file(proxy_file)

    def _resolve_proxy_file(self) -> Path | None:
        saved_proxy_file = self._saved_proxy_file()
        if saved_proxy_file is not None:
            return saved_proxy_file

        discovered_proxy_file = self._discover_proxy_file()
        if discovered_proxy_file is not None:
            self._save_proxy_file(discovered_proxy_file)
            return discovered_proxy_file

        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load SOCKS5 proxies",
            str(Path.home() / "Downloads"),
            "Text files (*.txt);;All files (*)",
        )
        if not file_name:
            return None

        proxy_file = Path(file_name)
        self._save_proxy_file(proxy_file)
        return proxy_file

    def _load_proxy_file(self, proxy_file: Path) -> None:
        try:
            proxy_text = proxy_file.read_text(encoding="utf-8")
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
        self.statusBar().showMessage(f"{summary} from {proxy_file.name}", 5000)

    def _saved_proxy_file(self) -> Path | None:
        try:
            value = self.proxy_source_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
            return None
        path = Path(value)
        return path if path.is_file() and path.suffix.lower() == ".txt" else None

    def _discover_proxy_file(self) -> Path | None:
        downloads = Path.home() / "Downloads"
        if not downloads.is_dir():
            return None

        candidates = [path for path in downloads.glob("Webshare*.txt") if path.is_file()]
        if not candidates:
            candidates = [path for path in downloads.glob("*.txt") if path.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _save_proxy_file(self, proxy_file: Path) -> None:
        try:
            self.proxy_source_file.write_text(str(proxy_file.resolve()), encoding="utf-8")
        except OSError:
            pass

    def clear_proxies(self) -> None:
        self._clear_all_emulator_proxies()
        self.proxies.clear()
        self.assignments.clear()
        self.proxy_checks.clear()
        self.windivert_guard.stop()
        self.routing.stop_all()
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

        assignment_mode, selected_proxy = self._choose_proxy_assignment(len(indexes))
        if assignment_mode is None:
            return

        if assignment_mode == "single":
            proxy = selected_proxy
            if proxy is None:
                return
            for instance_index in indexes:
                self._clear_emulator_proxy(instance_index)
                self.routing.stop(instance_index)
                self.assignments[instance_index] = proxy
                self.proxy_checks[instance_index] = self._check_proxy(proxy)
        else:
            for position, instance_index in enumerate(indexes):
                proxy_index = (self.proxy_cursor + position) % len(self.proxies)
                proxy = self.proxies[proxy_index]
                self._clear_emulator_proxy(instance_index)
                self.routing.stop(instance_index)
                self.assignments[instance_index] = proxy
                self.proxy_checks[instance_index] = self._check_proxy(proxy)
            self.proxy_cursor += len(indexes)
        self._update_windivert_guard()
        self._render_instances()
        self.statusBar().showMessage(f"Assigned proxies to {len(indexes)} instance(s)", 5000)

    def _choose_proxy_assignment(self, selected_count: int) -> tuple[str | None, ProxyConfig | None]:
        proxy_items = [f"{index + 1}. {proxy.display}" for index, proxy in enumerate(self.proxies)]
        items = proxy_items.copy()
        auto_label = "Auto assign different proxies"
        if selected_count > 1:
            items.insert(0, auto_label)

        choice, accepted = QInputDialog.getItem(
            self,
            "Assign proxy",
            "Select proxy:",
            items,
            0,
            False,
        )
        if not accepted:
            return None, None
        if choice == auto_label:
            return "auto", None

        proxy_index = proxy_items.index(choice)
        return "single", self.proxies[proxy_index]

    def start_proxy_routing(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        failures: list[str] = []
        applied_routes: list[str] = []
        started = 0
        for instance_index in indexes:
            proxy = self.assignments.get(instance_index)
            if proxy is None:
                failures.append(f"Instance {instance_index}: assign a proxy first")
                continue
            instance = self._instance_by_index(instance_index)
            if instance is None or instance.pid is None:
                failures.append(f"Instance {instance_index}: start LDPlayer before enabling WinDivert protection")
                continue
            status, proxy_ip = self._check_proxy(proxy)
            self.proxy_checks[instance_index] = (status, proxy_ip)
            if status != "Running":
                failures.append(f"Instance {instance_index}: proxy check failed ({status})")
                continue
            try:
                session = self.routing.start(instance_index, proxy)
                applied_proxy = self.provider.set_http_proxy(instance_index, session.listen_host, session.listen_port)
                applied_routes.append(f"Instance {instance_index}: {applied_proxy}")
                started += 1
            except Exception as exc:
                self.routing.stop(instance_index)
                failures.append(f"Instance {instance_index}: {exc}")

        self._update_windivert_guard()
        self._render_instances()
        if failures:
            QMessageBox.warning(self, "Some routing sessions failed", "\n".join(failures))
        if started:
            self._show_routing_status(started, applied_routes)

    def stop_proxy_routing(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            QMessageBox.information(self, "Select instances", "Select one or more emulator instances.")
            return

        for instance_index in indexes:
            self.routing.stop(instance_index)
            self._clear_emulator_proxy(instance_index)
        self._update_windivert_guard()
        self._render_instances()
        self.statusBar().showMessage(f"Stopped proxy routing for {len(indexes)} instance(s)", 5000)

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
        return check_proxy(proxy)

    def _protection_label(self) -> str:
        if self.windivert_guard.running:
            return "Protection: Kill switch on"
        return "Protection: WinDivert ready" if self.windivert_status.available else "Protection: Bridge only"

    def _show_routing_status(self, started: int, applied_routes: list[str]) -> None:
        applied_text = "\n".join(applied_routes[:8])
        if len(applied_routes) > 8:
            applied_text += f"\n...and {len(applied_routes) - 8} more"
        if self.windivert_guard.running:
            QMessageBox.information(
                self,
                "Routing protected",
                "Local authenticated proxy routing is running, and the WinDivert kill switch is active "
                "for LDPlayer-related process IDs.\n\n"
                f"Android proxy applied:\n{applied_text}\n\n"
                "Direct public TCP/UDP traffic from protected LDPlayer processes will be blocked. "
                "Transparent redirect is still the next layer.",
            )
        elif self.windivert_status.available:
            error = self.windivert_guard.stats.last_error
            message = "WinDivert is available, but the kill switch did not start."
            if error:
                message += f"\n\n{error}"
            QMessageBox.warning(self, "WinDivert not protecting", message)
        else:
            QMessageBox.warning(
                self,
                "WinDivert not active",
                "Local authenticated proxy routing is running, but WinDivert is not active yet.\n\n"
                f"{self.windivert_status.message}\n\n"
                "Until transparent redirection is added, LDPlayer traffic will not be forced through "
                "the proxy automatically.",
            )
        self.statusBar().showMessage(f"Started proxy routing for {started} instance(s)", 5000)

    def _update_windivert_guard(self) -> None:
        pids = self._active_routed_pids()
        if not pids:
            self.windivert_guard.stop()
            return
        if self.windivert_guard.running:
            self.windivert_guard.update_pids(pids)
        elif self.windivert_status.available:
            self.windivert_guard.start(pids)

    def _active_routed_pids(self) -> set[int]:
        routed_indexes = set(self.routing.sessions())
        instance_pids = {
            instance.pid
            for instance in self.instances
            if instance.index in routed_indexes and instance.pid is not None
        }
        if not instance_pids:
            return set()
        return instance_pids | ldplayer_related_pids()

    def _instance_by_index(self, instance_index: int) -> EmulatorInstance | None:
        return next((instance for instance in self.instances if instance.index == instance_index), None)

    def _clear_emulator_proxy(self, instance_index: int) -> None:
        try:
            self.provider.clear_http_proxy(instance_index)
        except Exception:
            pass

    def _clear_all_emulator_proxies(self) -> None:
        for instance_index in list(self.routing.sessions()):
            self._clear_emulator_proxy(instance_index)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        self._clear_all_emulator_proxies()
        self.windivert_guard.stop()
        self.routing.stop_all()
        super().closeEvent(event)
