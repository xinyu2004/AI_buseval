"""Main window. Menus only — no toolbar and no side property column."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QMimeData, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QHeaderView,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..lint import lint
from ..schema import IspVpac, Topology
from ..session import evaluate, load_preset, load_topology, preset_path, save_topology
from .canvas import GraphView
from .dialogs import CanDialog, DdrDialog, GmslDialog, KindDialog, PresetDialog, PropertyDialog
from .i18n import level_text, localize, set_lang, t, verdict_text
from .document import (
    add_block,
    auto_layout,
    ddr_level_rgb,
    find_node,
    iter_nodes,
    load_param_schema,
    new_topology,
    node_pos,
    palette_groups,
    remove_node,
    set_ddr_peer_port,
    set_ddr_port,
    set_node_pos,
    try_connect,
    type_color,
    update_node,
)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}".rstrip("0").rstrip(".")


def _ui_settings() -> QSettings:
    override = os.environ.get("BUSEVAL_UI_SETTINGS")
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings("buseval", "buseval")


def ui_language() -> str:
    value = str(_ui_settings().value("language", "zh") or "zh")
    return value if value in ("zh", "en") else "zh"


def remember_language(code: str) -> None:
    if code not in ("zh", "en"):
        return
    settings = _ui_settings()
    settings.setValue("language", code)
    settings.sync()


_DDR_BAR = "padding: 8px; background: #1f4e79; color: white; font-weight: 400;"
_DDR_BAR_UNSET = "padding: 10px; background: #b86a12; color: #fff8ee; font-weight: 600;"


def channel_has_peak(channel) -> bool:
    return channel.controller_mt_s is not None and channel.module_mt_s is not None


def ddr_bar_text(margin, yellow: float, red: float) -> str:
    """Read and write demands stay visible. The verdict follows their sum over available bandwidth."""
    demand = margin.read_demand_mbps + margin.write_demand_mbps
    occ = (demand / margin.available_mbps) if margin.available_mbps else float("inf")
    if occ >= red:
        code = "CRITICAL"
    elif occ >= yellow:
        code = "WARN"
    else:
        code = "OK"
    fields = {
        "read": f"{margin.read_demand_mbps:,.0f}",
        "write": f"{margin.write_demand_mbps:,.0f}",
        "demand": f"{demand:,.0f}",
        "occ": f"{occ * 100:.1f}",
        "yellow": _pct(yellow),
        "red": _pct(red),
        "verdict": verdict_text(code),
    }
    if code == "CRITICAL":
        why = t("ddr_why_bad").format(**fields)
    elif code == "WARN":
        why = t("ddr_why_warn").format(**fields)
    else:
        why = t("ddr_why_ok").format(**fields)
    return (
        f"{margin.name}  {t('peak')} {margin.available_mbps:,.0f} MB/s  "
        f"{t('read')} {margin.read_demand_mbps:,.0f} MB/s  "
        f"{t('write')} {margin.write_demand_mbps:,.0f} MB/s  "
        f"{t('ddr_occ').format(occ=fields['occ'])}\n"
        f"{why}"
    )


class PaletteList(QListWidget):
    def startDrag(self, supported):
        item = self.currentItem()
        if item is None or not item.data(Qt.ItemDataRole.UserRole):
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(item.data(Qt.ItemDataRole.UserRole)))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        set_lang(ui_language())
        self.setWindowTitle("buseval")
        self.resize(1280, 800)
        self.schema = load_param_schema()
        self.topology: Topology = new_topology()
        self.path: Path | None = None
        self.dirty = False
        self.evaluated = False
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._moving = False
        self.measured_notes: dict[str, str] = {}
        self.preset_name: str | None = None
        self.at_home = True
        self.show_palette = True
        self.show_bottom = True
        self.last_result = None
        self.ddr_mode = "pending"
        self._compare_rows = []
        self._did_lint = False
        self.setStyleSheet(
            "QMainWindow { background: #d5e0ec; }"
            "QListWidget { background: #f4f7fb; color: #1c2833; border: none; }"
            "QStatusBar { background: #c5d4e4; color: #1c2833; }"
            "QTabWidget::pane { background: #f4f7fb; border-top: 1px solid #b7c6d6; }"
            "QTabBar { background: #f4f7fb; }"
            "QTabBar::tab { background: #d5e0ec; padding: 4px 12px; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #f4f7fb; }"
            "QTableWidget { background: #f7fafc; gridline-color: #d5e0ec; }"
            "QSplitter::handle { background: #b7c6d6; }"
        )

        self.canvas = GraphView(self)
        self.palette = PaletteList()
        self.palette.setDragEnabled(True)
        self.palette.setMinimumWidth(200)
        self.palette.setMaximumWidth(320)
        self.palette.setFont(QFont("", 13))
        self.palette.setIconSize(QSize(14, 14))
        self.palette.setStyleSheet(
            "QListWidget { background: #f4f7fb; color: #1c2833; border: none; }"
            "QListWidget::item { padding: 3px 2px; }"
        )
        self._fill_palette()
        self.palette.viewport().setAcceptDrops(False)

        self.ddr_bar = QLabel("")
        self.ddr_bar.setWordWrap(True)
        self.ddr_bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.ddr_bar.setStyleSheet(_DDR_BAR)

        self.lint_table = QTableWidget(0, 3)
        self.lint_table.setHorizontalHeaderLabels([t("level"), t("rule"), t("detail")])
        self.lint_table.cellDoubleClicked.connect(self._focus_lint)
        self.assume_table = QTableWidget(0, 3)
        self.assume_table.setHorizontalHeaderLabels([t("lv"), t("item"), t("detail")])
        self.assume_table.cellDoubleClicked.connect(self._focus_named_row)
        self.compare_table = QTableWidget(0, 6)
        self.compare_table.setHorizontalHeaderLabels([
            t("compare_name"), t("compare_pred"), t("compare_meas"),
            t("compare_rerr"), t("compare_werr"), t("compare_verdict"),
        ])
        self.compare_table.cellDoubleClicked.connect(self._focus_named_row)
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setMaximumHeight(168)
        tabs.addTab(self.lint_table, t("tab_lint"))
        tabs.addTab(self.assume_table, t("tab_assume"))
        tabs.addTab(self.compare_table, t("tab_compare"))
        for table in (self.lint_table, self.assume_table, self.compare_table):
            table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabs = tabs

        center = QWidget()
        column = QVBoxLayout(center)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.canvas, 1)
        column.addWidget(self.ddr_bar)
        split = QSplitter()
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        split.addWidget(self.palette)
        split.addWidget(center)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1060])
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        outer_layout.addWidget(split, 1)
        outer_layout.addWidget(tabs)
        self.setCentralWidget(outer)
        self.setStatusBar(QStatusBar())

        self.focus_card = QLabel("", self.canvas)
        self.focus_card.setWordWrap(True)
        self.focus_card.setStyleSheet(
            "background: rgba(255,255,255,235); border: 2px solid #e67e22; padding: 8px;"
        )
        self.focus_card.hide()
        self._asked_kind = False
        self._build_menus()
        self._show_topology(keep_positions=False)
        self._refresh_title()
        self.ddr_bar.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "lint_table"):
            for table in (self.lint_table, self.assume_table, self.compare_table):
                self._fit_table(table)
        if not self._asked_kind:
            self._asked_kind = True
            QTimer.singleShot(0, self._choose_kind_when_placed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "lint_table"):
            for table in (self.lint_table, self.assume_table, self.compare_table):
                self._fit_table(table)

    def _clear_reports(self):
        self._did_lint = False
        self._lint_warnings = []
        self._compare_rows = []
        self.lint_table.setRowCount(0)
        self.assume_table.setRowCount(0)
        self.compare_table.setRowCount(0)

    def _fit_table(self, table: QTableWidget):
        """Keep every column inside the panel. Short columns follow their text; the last one wraps."""
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        count = table.columnCount()
        last = count - 1
        viewport = table.viewport().width()
        if viewport <= 0:
            header.setSectionResizeMode(last, QHeaderView.ResizeMode.Stretch)
            return
        fm = table.fontMetrics()
        widths = []
        for col in range(last):
            label = table.horizontalHeaderItem(col)
            width = fm.horizontalAdvance(label.text() if label is not None else "") + 28
            for row in range(table.rowCount()):
                item = table.item(row, col)
                if item is not None:
                    width = max(width, fm.horizontalAdvance(item.text()) + 28)
            widths.append(width)
        floor = 96
        others = sum(widths)
        if others > viewport - floor:
            room = max(viewport - floor, last * 48)
            scale = room / others if others else 1
            widths = [max(48, int(width * scale)) for width in widths]
            others = sum(widths)
        for col, width in enumerate(widths):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(col, width)
        header.setSectionResizeMode(last, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()

    def _fill_palette(self):
        group_labels = {"外设": t("group_periph"), "Pipeline": t("group_pipeline")}
        for group, types in palette_groups(self.schema):
            header = QListWidgetItem(group_labels.get(group, group))
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.palette.addItem(header)
            for type_name in types:
                item = QListWidgetItem(type_name)
                item.setData(Qt.ItemDataRole.UserRole, type_name)
                swatch = QPixmap(14, 14)
                swatch.fill(QColor(type_color(type_name)))
                item.setIcon(QIcon(swatch))
                self.palette.addItem(item)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu(t("file"))
        self._action(file_menu, t("new"), self.choose_kind, QKeySequence.StandardKey.New)
        self._action(file_menu, t("can_report"), self.open_can)
        self._action(file_menu, t("gmsl_link"), self.open_gmsl)
        self._action(file_menu, t("open_preset"), self.choose_preset)
        self._action(file_menu, t("open_yaml"), self.open_yaml, QKeySequence.StandardKey.Open)
        self._action(file_menu, t("save"), self.save, QKeySequence.StandardKey.Save)
        self._action(file_menu, t("save_as"), self.save_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        self._action(file_menu, t("quit"), self.close, QKeySequence.StandardKey.Quit)

        eval_menu = self.menuBar().addMenu(t("evaluate_menu"))
        self._action(eval_menu, t("evaluate"), self.run_evaluate, QKeySequence("Ctrl+Enter"))
        self._action(eval_menu, t("lint"), self.run_lint, QKeySequence("Ctrl+V"))

        measure = self.menuBar().addMenu(t("measure"))
        self._action(measure, t("collect"), self.collect_local)
        self._action(measure, t("import_meas"), self.import_measurement)

        view = self.menuBar().addMenu(t("view"))
        self._action(view, t("fit"), self.canvas.fit)
        palette = QAction(t("show_palette"), self)
        palette.setCheckable(True)
        palette.setChecked(self.show_palette)
        palette.triggered.connect(self._toggle_palette)
        view.addAction(palette)
        bottom = QAction(t("show_bottom"), self)
        bottom.setCheckable(True)
        bottom.setChecked(self.show_bottom)
        bottom.triggered.connect(self._toggle_bottom)
        view.addAction(bottom)
        self._action(view, t("undo"), self.undo, QKeySequence.StandardKey.Undo)
        self._action(view, t("redo"), self.redo, QKeySequence.StandardKey.Redo)
        language = view.addMenu(t("language"))
        language.addAction("中文", lambda: self.set_language("zh"))
        language.addAction("English", lambda: self.set_language("en"))

    def _toggle_palette(self, checked):
        self.show_palette = bool(checked)
        self.palette.setVisible(self.show_palette)

    def _toggle_bottom(self, checked):
        self.show_bottom = bool(checked)
        self.tabs.setVisible(self.show_bottom)

    def set_language(self, code: str):
        if code not in ("zh", "en"):
            return
        set_lang(code)
        remember_language(code)
        self.menuBar().clear()
        self._build_menus()
        self.palette.clear()
        self._fill_palette()
        self.lint_table.setHorizontalHeaderLabels([t("level"), t("rule"), t("detail")])
        self.assume_table.setHorizontalHeaderLabels([t("lv"), t("item"), t("detail")])
        self.compare_table.setHorizontalHeaderLabels([
            t("compare_name"), t("compare_pred"), t("compare_meas"),
            t("compare_rerr"), t("compare_werr"), t("compare_verdict"),
        ])
        self.tabs.setTabText(0, t("tab_lint"))
        self.tabs.setTabText(1, t("tab_assume"))
        self.tabs.setTabText(2, t("tab_compare"))
        self._refresh_title()
        self._paint_ddr()
        if self._compare_rows:
            self._fill_compare(self._compare_rows)
        self._show_topology(keep_positions=True)
        if self.last_result is not None:
            self._fill_assumptions(self.last_result)
            self.canvas.apply_prediction(self.last_result, self.measured_notes)
        if self._did_lint and not self.at_home:
            self._fill_lint(lint(self.topology), announce=False)
        else:
            self.lint_table.setRowCount(0)
        for table in (self.lint_table, self.assume_table, self.compare_table):
            self._fit_table(table)

    def _action(self, menu, label, slot, shortcut=None):
        action = QAction(label, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _snapshot(self):
        self.undo_stack.append(self.topology.model_dump())
        self.redo_stack.clear()
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.topology.model_dump())
        self.topology = Topology.model_validate(self.undo_stack.pop())
        self._invalidate()
        self._show_topology(keep_positions=True)

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.topology.model_dump())
        self.topology = Topology.model_validate(self.redo_stack.pop())
        self._invalidate()
        self._show_topology(keep_positions=True)

    def _mark_dirty(self):
        self.dirty = True
        self._refresh_title()

    def _refresh_title(self):
        if self.path is not None:
            name = self.path.name
        elif self.preset_name:
            name = self.preset_name
        else:
            name = t("unnamed")
        star = f"  {t('unsaved')}" if self.dirty else ""
        self.setWindowTitle(f"buseval  {name}{star}")

    def hover_node(self, item):
        if item is None:
            self.focus_card.hide()
            return
        self.update_focus_card(item.node_name, item)

    def update_focus_card(self, name: str | None, item=None):
        if not name or item is None:
            self.focus_card.hide()
            return
        lines = []
        stats = self.canvas.node_stats.get(name)
        if stats:
            share, read, write = stats
            lines.append(f"{name}    {t('share')} {share * 100:.1f}%")
            lines.append(f"{t('read')} {read:.0f} MB/s    {t('write')} {write:.0f} MB/s")
        else:
            lines.append(name)
            lines.append(t("not_evaluated"))
        self.focus_card.setText("\n".join(lines))
        self.focus_card.setMaximumWidth(max(160, self.canvas.width() - 24))
        self.focus_card.adjustSize()
        rect = item.sceneBoundingRect()
        anchor = self.canvas.mapFromScene(rect.topRight())
        x = anchor.x() + 12
        y = anchor.y() + 36
        if x + self.focus_card.width() > self.canvas.width() - 8:
            left = self.canvas.mapFromScene(rect.topLeft())
            x = left.x() - self.focus_card.width() - 12
        y = max(8, min(y, self.canvas.height() - self.focus_card.height() - 8))
        self.focus_card.move(max(8, x), y)
        self.focus_card.show()
        self.focus_card.raise_()

    def _show_topology(self, keep_positions: bool):
        if not keep_positions:
            auto_layout(self.topology)
        self.canvas.rebuild(self.topology)

    def _invalidate(self):
        if self.evaluated:
            self.canvas.clear_colors()
            self.evaluated = False
            self.measured_notes = {}
            self.ddr_mode = "pending"
            self._paint_ddr()
            self.last_result = None

    def _choose_kind_when_placed(self):
        handle = self.windowHandle()
        if handle is not None and not handle.isExposed():
            QTimer.singleShot(0, self._choose_kind_when_placed)
            return
        self.choose_kind()

    def _place_on_window(self, dialog):
        dialog.adjustSize()
        screen = self.screen()
        if screen is not None:
            dialog.setScreen(screen)
        frame = self.frameGeometry()
        if screen is not None and (not frame.isValid() or frame.width() < 50):
            frame = screen.availableGeometry()
        dialog.move(frame.center() - dialog.rect().center())

    def choose_kind(self):
        kind = "soc"
        source = "common"
        while True:
            dialog = KindDialog(self, kind=kind, source=source)
            self._place_on_window(dialog)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            kind = dialog.chosen()
            source = dialog.source()
            if kind == "can":
                self.open_can()
                return
            if kind == "gmsl":
                self.open_gmsl()
                return
            if kind != "soc":
                return
            if source == "blank":
                self._start_soc()
                return
            if source == "common" and self.choose_preset():
                return
            if source == "private" and self.open_yaml():
                return

    def choose_preset(self) -> bool:
        dialog = PresetDialog(self)
        self._place_on_window(dialog)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return False
        name = dialog.chosen()
        if not name:
            return False
        self.open_preset(name)
        return True

    def _start_soc(self):
        self._snapshot()
        self.topology = new_topology()
        self.path = None
        self.preset_name = None
        self.at_home = False
        self.dirty = True
        self._invalidate()
        self._clear_reports()
        self._show_topology(keep_positions=False)
        self.ddr_mode = "unset"
        self._paint_ddr()
        self._refresh_title()
        self.statusBar().showMessage(t("blank_soc"))

    def open_yaml(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(self, t("open_topology"), "", "YAML (*.yaml *.yml)")
        if not path:
            return False
        self._show_loaded(load_topology(path), Path(path), None, str(path))
        return True

    def open_preset(self, name: str):
        self._show_loaded(load_preset(name), preset_path(name), name, name)

    def _show_loaded(self, topology: Topology, path, preset_name: str | None, message: str):
        """Open one topology. Language stays with the user, not the file."""
        self.topology = topology
        self.path = Path(path) if path is not None else None
        self.preset_name = preset_name
        self.at_home = False
        self.dirty = False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._invalidate()
        self._clear_reports()
        channel = self.topology.ddr_channels[0] if self.topology.ddr_channels else None
        self.ddr_mode = "unset" if channel is None or not channel_has_peak(channel) else "pending"
        self._paint_ddr()
        self._show_topology(keep_positions=False)
        self._refresh_title()
        self.statusBar().showMessage(message)

    def _ready_to_save(self) -> bool:
        """Lint errors and a failed prediction block the write. Warnings do not."""
        self._did_lint = True
        errors = self._fill_lint(lint(self.topology), announce=False)
        if errors:
            self.tabs.setCurrentWidget(self.lint_table)
            self.statusBar().showMessage(t("save_blocked").format(message=errors[0].message))
            return False
        try:
            evaluate(self.topology)
        except ValueError as exc:
            self.statusBar().showMessage(t("save_blocked").format(message=str(exc)))
            return False
        return True

    def save(self):
        if self.path is None:
            self.save_as()
            return
        if not self._ready_to_save():
            return
        save_topology(self.topology, self.path)
        self.dirty = False
        self._refresh_title()
        self.statusBar().showMessage(t("saved").format(path=self.path))

    def save_as(self):
        if not self._ready_to_save():
            return
        path, _ = QFileDialog.getSaveFileName(self, t("save_as"), "topology.yaml", "YAML (*.yaml *.yml)")
        if not path:
            return
        self.path = Path(path)
        self.save()

    def add_block_at(self, type_name: str, x: float, y: float):
        if type_name not in self.schema:
            return
        self._snapshot()
        add_block(self.topology, type_name, x, y, self.schema)
        self._mark_dirty()
        self._invalidate()
        self._show_topology(keep_positions=True)

    def remember_pos(self, name: str, x: float, y: float):
        set_node_pos(self.topology, name, x, y)
        self._mark_dirty()

    def remember_ddr_port(self, node_name: str, peer: str | None, side: str, u: float):
        if peer is None:
            set_ddr_port(self.topology, node_name, side, u)
        else:
            set_ddr_peer_port(self.topology, node_name, peer, side, u)
        self._mark_dirty()

    def connect_nodes(self, src: str, dst: str):
        self._snapshot()
        error = try_connect(self.topology, src, dst)
        if error:
            self.undo_stack.pop()
            self.statusBar().showMessage(error)
            return
        self._mark_dirty()
        self._invalidate()
        self._show_topology(keep_positions=True)

    def delete_node(self, name: str):
        if any(ch.name == name for ch in self.topology.ddr_channels):
            return
        self._snapshot()
        remove_node(self.topology, name)
        self._mark_dirty()
        self._invalidate()
        self._show_topology(keep_positions=True)

    def edit_node(self, name: str):
        if any(ch.name == name for ch in self.topology.ddr_channels):
            self._open_ddr(None)
            return
        node = find_node(self.topology, name)
        if node is None:
            return
        if str(node.type).startswith("can"):
            dialog = CanDialog(self, node)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            values = dialog.node_values()
            self._snapshot()
            error = update_node(
                self.topology,
                name,
                new_name=values["name"] or name,
                enabled=values["enabled"],
                params=values["params"],
                mode=values["mode"],
                stages=values["stages"],
            )
            if error:
                self.undo_stack.pop()
                self.statusBar().showMessage(error)
                return
            self._mark_dirty()
            self._invalidate()
            self._show_topology(keep_positions=True)
            return
        dialog = PropertyDialog(node, self.schema.get(node.type, {}), self, self.topology)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.result_values()
        self._snapshot()
        error = update_node(
            self.topology,
            name,
            new_name=values["name"] or name,
            enabled=values["enabled"],
            params=values["params"],
            mode=values["mode"],
            stages=values["stages"],
        )
        if error:
            self.undo_stack.pop()
            self.statusBar().showMessage(error)
            return
        chip = values.get("chip")
        if chip and chip.get("name"):
            slots = list(self.topology.isp_vpacs)
            for slot in slots:
                if slot.name == chip["name"]:
                    slot.mpix_s = chip["mpix_s"]
                    break
            else:
                slots.append(IspVpac(name=chip["name"], mpix_s=chip["mpix_s"]))
            self.topology.isp_vpacs = slots
        self._mark_dirty()
        self._invalidate()
        self._show_topology(keep_positions=True)

    def _open_ddr(self, event):
        from ..schema import DDRChannel
        added = False
        if not self.topology.ddr_channels:
            self.topology.ddr_channels.append(
                DDRChannel(name="DDR0")
            )
            added = True
        dialog = DdrDialog(self.topology, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._snapshot()
            dialog.apply()
            self._mark_dirty()
            self._invalidate()
            self._show_topology(keep_positions=True)
            return
        if added:
            self.topology.ddr_channels.clear()

    def _paint_ddr(self):
        if self.at_home:
            self.ddr_bar.hide()
            self.ddr_bar.setText("")
            return
        self.ddr_bar.show()
        self.ddr_bar.setStyleSheet(_DDR_BAR)
        if self.ddr_mode == "result" and self.last_result and self.last_result.margins:
            margin = self.last_result.margins[0]
            yellow = float(self.topology.alert_thresholds.get("yellow", 0.6))
            red = float(self.topology.alert_thresholds.get("red", 0.8))
            demand = margin.read_demand_mbps + margin.write_demand_mbps
            occ = (demand / margin.available_mbps) if margin.available_mbps else 0.0
            red_c, green_c, blue_c = ddr_level_rgb(occ, yellow, red)
            self.ddr_bar.setStyleSheet(
                f"padding: 8px; background: #{red_c:02x}{green_c:02x}{blue_c:02x}; "
                "color: #1a1a1a; font-weight: 400;"
            )
            self.ddr_bar.setText(ddr_bar_text(margin, yellow, red))
            return
        if self.topology.ddr_channels and not channel_has_peak(self.topology.ddr_channels[0]):
            channel = self.topology.ddr_channels[0]
            self.ddr_bar.setStyleSheet(_DDR_BAR_UNSET)
            self.ddr_bar.setText(f"{channel.name}  {t('ddr_bar_unset')}")
            return
        if self.ddr_mode == "none":
            self.ddr_bar.setText(t("ddr_none"))
            return
        self.ddr_bar.setText(t("ddr_pending"))

    def run_lint(self) -> bool:
        if self.at_home:
            self.statusBar().showMessage(t("open_first"))
            return False
        self._did_lint = True
        errors = self._fill_lint(lint(self.topology), announce=True)
        self.tabs.setCurrentWidget(self.lint_table)
        return not errors

    def _fill_lint(self, issues, announce: bool) -> list:
        self.lint_table.setRowCount(0)
        errors = []
        for issue in issues:
            row = self.lint_table.rowCount()
            self.lint_table.insertRow(row)
            values = (level_text(issue.level), issue.rule, issue.message)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, issue.node or "")
                cell.setData(Qt.ItemDataRole.UserRole + 1, issue.rule)
                self.lint_table.setItem(row, col, cell)
            if issue.level == "error":
                errors.append(issue)
        self.canvas.mark_errors({issue.node for issue in errors if issue.node})
        if announce and errors:
            self.statusBar().showMessage(errors[0].message)
        self._lint_warnings = [i for i in issues if i.level == "warning"]
        self._fit_table(self.lint_table)
        return errors

    def _fill_assumptions(self, result):
        self.assume_table.setRowCount(0)
        for row_data in result.assumptions:
            row = self.assume_table.rowCount()
            self.assume_table.insertRow(row)
            cells = (
                level_text(str(row_data.get("level", ""))),
                str(row_data.get("item", "")),
                localize(str(row_data.get("message", ""))),
            )
            name = str(row_data.get("item", ""))
            for col, value in enumerate(cells):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, name)
                self.assume_table.setItem(row, col, cell)
        self._fit_table(self.assume_table)

    def _focus_named_row(self, row, _col):
        table = self.sender()
        item = table.item(row, 0) if table is not None else None
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self._open_named(name)

    def _open_named(self, name: str):
        if not name:
            return
        if any(channel.name == name for channel in self.topology.ddr_channels):
            self._open_ddr(None)
            return
        node = self.canvas.nodes.get(name)
        if node is not None:
            self.canvas.centerOn(node)
            node.setSelected(True)
        if find_node(self.topology, name) is not None:
            self.edit_node(name)

    def _focus_lint(self, row, _col):
        message = self.lint_table.item(row, 2)
        if message is None:
            return
        name = message.data(Qt.ItemDataRole.UserRole) or ""
        rule = message.data(Qt.ItemDataRole.UserRole + 1) or ""
        if rule == "no-ddr" or (not name and "DDR" in message.text()):
            self._open_ddr(None)
            return
        node = self.canvas.nodes.get(name)
        if node is not None:
            self.canvas.centerOn(node)
            node.setSelected(True)
        if name:
            self.edit_node(name)

    def run_evaluate(self):
        if not self.run_lint():
            return
        try:
            result = evaluate(self.topology)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self.evaluated = True
        self.last_result = result
        self.canvas.apply_prediction(result, self.measured_notes)
        self._fill_assumptions(result)
        self.ddr_mode = "result" if result.margins else "none"
        self._paint_ddr()
        warnings = getattr(self, "_lint_warnings", [])
        if warnings:
            self.tabs.setCurrentWidget(self.lint_table)
            self.statusBar().showMessage(t("eval_warn").format(n=len(warnings)))
        else:
            self.tabs.setCurrentWidget(self.assume_table)
            self.statusBar().showMessage(t("eval_done"))

    def selected_node_name(self) -> str | None:
        from .canvas import NodeItem
        for item in self.canvas.scene().selectedItems():
            if isinstance(item, NodeItem):
                return item.node_name
        return None

    def open_can(self):
        CanDialog(self).exec()

    def open_gmsl(self):
        GmslDialog(self).exec()

    def collect_local(self):
        from ..collect.runner import run_collect
        path, _ = QFileDialog.getSaveFileName(self, t("save_meas"), "meas.json", "JSON (*.json)")
        if not path:
            return
        try:
            run_collect("arm_pc", path)
        except Exception as exc:
            self.statusBar().showMessage(str(exc))
            return
        self.statusBar().showMessage(t("wrote_node").format(name=path))
        self._import_path(path)

    def import_measurement(self):
        if self.dirty:
            box = QMessageBox(self)
            box.setWindowTitle(t("unsaved"))
            box.setText(t("save_before_import"))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.button(QMessageBox.StandardButton.Yes).setText(t("yes"))
            box.button(QMessageBox.StandardButton.No).setText(t("no"))
            answer = box.exec()
            if answer == QMessageBox.StandardButton.Yes:
                self.save()
                if self.dirty:
                    return
        path, _ = QFileDialog.getOpenFileName(self, t("import_title"), "", "JSON (*.json)")
        if path:
            self._import_path(path)

    def _fill_compare(self, rows):
        self.compare_table.setRowCount(0)
        self.measured_notes = {}
        for row_data in rows:
            row = self.compare_table.rowCount()
            self.compare_table.insertRow(row)
            pred = ""
            meas = ""
            if row_data.predicted_read is not None:
                pred = f"{row_data.predicted_read:.1f} / {row_data.predicted_write:.1f}"
            if row_data.measured_read is not None:
                meas = f"{row_data.measured_read:.1f} / {row_data.measured_write:.1f}"
            read_err = "" if row_data.read_error_pct is None else f"{row_data.read_error_pct * 100:.1f}%"
            write_err = "" if row_data.write_error_pct is None else f"{row_data.write_error_pct * 100:.1f}%"
            note = localize(row_data.note) if row_data.note else ""
            cells = [row_data.name, pred, meas, read_err, write_err, f"{verdict_text(row_data.verdict)} {note}".strip()]
            for col, value in enumerate(cells):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, row_data.name)
                self.compare_table.setItem(row, col, cell)
            if row_data.measured_read is not None and row_data.note != "实测项不在拓扑中":
                err = row_data.read_error_pct
                extra = "" if err is None else f" {err * 100:.0f}%"
                self.measured_notes[row_data.name] = t("meas_overlay").format(
                    read=row_data.measured_read, write=row_data.measured_write, extra=extra,
                )
        self._fit_table(self.compare_table)

    def _import_path(self, path: str):
        from ..engine.compare import compare_measurement, load_measurement
        try:
            measurement = load_measurement(path)
            result = evaluate(self.topology)
            rows = compare_measurement(result, measurement)
        except Exception as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._compare_rows = rows
        self._fill_compare(rows)
        self.evaluated = True
        self.last_result = result
        self.ddr_mode = "result" if result.margins else "none"
        self._paint_ddr()
        self.canvas.apply_prediction(result, self.measured_notes)
        self.tabs.setCurrentWidget(self.compare_table)
        self.statusBar().showMessage(t("imported").format(path=path))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
