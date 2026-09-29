"""Property, DDR, CAN, and GMSL dialogs. They edit models; they do not predict."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..engine.graph import normalize_source
from ..estimators.formats import CUSTOM_FORMAT, bpp_for_format, format_choices, format_for_link
from .document import ddr_level_rgb, find_node
from ..estimators.registry import get_coefficients
from ..gmsl.calculator import MAX_STREAMS, coax_tier_rows, link_options, load_yaml, save_yaml
from ..gmsl.text import (
    bandwidth_line,
    csi_text,
    gbps_label,
    link_steps,
    multi_steps,
)
from ..dbc.health_report import latency_text
from ..session import can_health, list_presets, preset_path
from .i18n import lang, t


def _field_tip(field: dict) -> str:
    if lang() == "en":
        return str(field.get("tip_en") or field.get("tip") or "")
    return str(field.get("tip") or field.get("tip_en") or "")


def _choices(field: dict) -> list[str]:
    if field.get("choices"):
        return [str(c) for c in field["choices"]]
    dotted = field.get("choices_from")
    if not dotted:
        return []
    cursor = get_coefficients()
    for part in dotted.split("."):
        cursor = cursor.get(part, {})
    if isinstance(cursor, dict):
        return [str(k) for k in cursor.keys()]
    return []


def _spin_float(value, minimum=-1e12, maximum=1e12):
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(4)
    box.setValue(float(value or 0))
    return box


def _spin_int(value):
    box = QSpinBox()
    box.setRange(0, 2_000_000_000)
    box.setValue(int(value or 0))
    return box


def _adapt_table(table: QTableWidget):
    """Fill the dialog width. The last column wraps instead of growing a horizontal scrollbar."""
    table.setWordWrap(True)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    last = table.columnCount() - 1
    header.setSectionResizeMode(last, QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.resizeRowsToContents()


def _label_box(buttons, ok_key="ok"):
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if ok is not None:
        ok.setText(t(ok_key))
    if cancel is not None:
        cancel.setText(t("cancel"))
    return buttons


# Home 「新建」 choices.
EVAL_KINDS = (
    ("soc", "kind_soc", "kind_soc_blurb"),
    ("can", "kind_can", "kind_can_blurb"),
    ("gmsl", "kind_gmsl", "kind_gmsl_blurb"),
)


class KindDialog(QDialog):
    """Choose what a new evaluation is. SoC also picks a blank canvas, a common SoC, or a private one."""

    def __init__(self, parent=None, kind: str = "soc", source: str = "common"):
        super().__init__(parent)
        self.setWindowTitle(t("kind_title"))
        self.resize(480, 340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("kind_prompt")))
        self.names = QListWidget()
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        for kind_id, title_key, blurb_key in EVAL_KINDS:
            item = QListWidgetItem(t(title_key))
            item.setData(Qt.ItemDataRole.UserRole, kind_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, t(blurb_key))
            self.names.addItem(item)
        self.sources = QWidget()
        source_layout = QVBoxLayout(self.sources)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self._source_group = QButtonGroup(self)
        self._source_buttons = {}
        for source_id, key in (("blank", "soc_blank"), ("common", "open_preset"), ("private", "open_yaml")):
            button = QRadioButton(t(key))
            self._source_group.addButton(button)
            self._source_buttons[source_id] = button
            source_layout.addWidget(button)
        self._source_buttons.get(source, self._source_buttons["common"]).setChecked(True)
        self.names.currentItemChanged.connect(self._show)
        self.names.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.names, 1)
        layout.addWidget(self.detail)
        layout.addWidget(self.sources)
        row = 0
        for index in range(self.names.count()):
            if self.names.item(index).data(Qt.ItemDataRole.UserRole) == kind:
                row = index
                break
        self.names.setCurrentRow(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _label_box(buttons, "start")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show(self, current, _previous):
        kind = "" if current is None else str(current.data(Qt.ItemDataRole.UserRole) or "")
        self.sources.setVisible(kind == "soc")
        if kind == "soc" or current is None:
            self.detail.hide()
            self.detail.setText("")
            return
        self.detail.show()
        self.detail.setText(current.data(Qt.ItemDataRole.UserRole + 1) or "")

    def chosen(self) -> str:
        item = self.names.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def source(self) -> str:
        for source_id, button in self._source_buttons.items():
            if button.isChecked():
                return source_id
        return "common"


class PresetDialog(QDialog):
    """Pick a named preset. Nothing opens until the user confirms."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("preset_title"))
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("preset_prompt")))
        self.names = QListWidget()
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        for name in list_presets():
            self.names.addItem(name)
        self.names.currentTextChanged.connect(self._show)
        self.names.itemDoubleClicked.connect(lambda _item: self.accept())
        if self.names.count():
            self.names.setCurrentRow(0)
        layout.addWidget(self.names, 1)
        layout.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _label_box(buttons, "open")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show(self, name: str):
        if not name:
            self.detail.setText("")
            return
        first = preset_path(name).read_text(encoding="utf-8").splitlines()[0]
        self.detail.setText(first.lstrip("# ").strip())

    def chosen(self) -> str:
        item = self.names.currentItem()
        return item.text() if item is not None else ""


def _format_editor(current, bpp=None, allow_blank=False):
    """Shared format list. A known format shows gray bpp; custom lets the user type it."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    box = QComboBox()
    if allow_blank:
        box.addItem("")
    box.addItems(format_choices())
    if current and box.findText(str(current)) >= 0:
        box.setCurrentText(str(current))
    bits = QLabel("")
    bits.setStyleSheet("color: #4a4a4a;")
    spin = _spin_float(bpp or 12, 1, 64)
    spin.setDecimals(2)
    spin.setSuffix(" bpp")

    def show(name, label=bits, editor=spin):
        custom = name == CUSTOM_FORMAT
        label.setVisible(not custom and bool(name))
        editor.setVisible(custom)
        if not custom:
            value = bpp_for_format(name)
            label.setText("" if value is None else f"{value:g} bpp")

    box.currentTextChanged.connect(show)
    show(box.currentText())
    layout.addWidget(box)
    layout.addWidget(bits)
    layout.addWidget(spin)
    row._format_box = box  # type: ignore[attr-defined]
    row._bpp_spin = spin  # type: ignore[attr-defined]
    return row


def _picture_size(node, topology) -> tuple[int | None, int | None]:
    """Resolution to show in an empty stage: the connected picture, else this node's own."""
    if topology is not None:
        for name in normalize_source(getattr(node, "source", None)):
            src = find_node(topology, name)
            if src is None:
                continue
            params = src.params or {}
            if params.get("width") and params.get("height"):
                return int(params["width"]), int(params["height"])
            stages = getattr(src, "stages", None) or []
            if stages and stages[-1].width and stages[-1].height:
                return int(stages[-1].width), int(stages[-1].height)
    params = node.params or {}
    if params.get("width") and params.get("height"):
        return int(params["width"]), int(params["height"])
    return None, None


class PropertyDialog(QDialog):
    def __init__(self, node, spec: dict, parent=None, topology=None):
        super().__init__(parent)
        self.setWindowTitle(f"{t('prop_title')} — {node.name}")
        self._spec = spec
        self._widgets = {}
        self._keep_enabled = bool(node.enabled)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(node.name)
        self._bind_tip(self.name_edit, t("name_tip"))
        form.addRow(t("name"), self.name_edit)
        kind = QLabel(node.type)
        kind.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        form.addRow(t("type"), kind)
        params = dict(node.params or {})
        self._stage_size = _picture_size(node, topology)
        if node.type == "isp":
            self._add_chip_row(form, topology)
        self._ratios = {}
        if node.type in ("venc", "vdec"):
            self._ratios = {
                str(k): float(v)
                for k, v in get_coefficients().get(node.type, {}).get("compression_ratios", {}).items()
            }
        self._auto_ratio = None
        self._ratio_hint = None
        for field in spec.get("fields") or []:
            key = field["key"]
            target = field.get("target", "params")
            tip = _field_tip(field)
            if field["widget"] == "stages":
                self.stages = QTableWidget(0, 6)
                self.stages.setHorizontalHeaderLabels(
                    ["name", "format", "width", "height", "read_factor", "write_factor"]
                )
                for stage in node.stages:
                    width = stage.width or self._stage_size[0]
                    height = stage.height or self._stage_size[1]
                    self._add_stage_row(
                        stage.name, stage.format or "", width, height,
                        stage.read_factor, stage.write_factor, stage.bpp,
                    )
                add = QPushButton(t("add_stage"))
                add.clicked.connect(self._append_stage)
                self._bind_tip(self.stages, tip)
                self._bind_tip(add, tip)
                self.stages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                form.addRow(self._label(key, tip), self.stages)
                form.addRow("", add)
                continue
            if target == "mode":
                current = node.mode
            elif key not in params and "default" in field:
                current = field["default"]
            else:
                current = params.get(key, "")
            if field["widget"] == "format":
                if not current:
                    current = field.get("default") or ""
                current = (current, params.get("bpp"))
            widget = self._make_widget(field, current)
            if field.get("locked"):
                _lock_widget(widget)
            self._widgets[key] = (field, widget)
            self._bind_tip(widget, tip)
            if field["widget"] == "file":
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(widget, 1)
                browse = QPushButton(t("browse"))
                browse.clicked.connect(lambda _=False, edit=widget: self._browse_file(edit))
                row_layout.addWidget(browse)
                form.addRow(self._label(key, tip), row)
            elif key == "compression_ratio":
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(widget)
                self._ratio_hint = QLabel("")
                self._ratio_hint.setWordWrap(True)
                row_layout.addWidget(self._ratio_hint, 1)
                form.addRow(self._label(key, tip), row)
            else:
                form.addRow(self._label(key, tip), widget)
        self._bind_codec_ratio(params.get("compression_ratio"))
        layout.addLayout(form, 1 if hasattr(self, "stages") else 0)
        if hasattr(self, "stages"):
            self._fit_stages()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _label_box(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _label(self, key: str, tip: str) -> QLabel:
        label = QLabel(key)
        if tip:
            label.setToolTip(tip)
        return label

    def _bind_tip(self, widget, tip: str):
        if tip:
            widget.setToolTip(tip)

    def _bind_codec_ratio(self, saved):
        codec_entry = self._widgets.get("codec")
        ratio_entry = self._widgets.get("compression_ratio")
        if codec_entry is None or ratio_entry is None or not self._ratios:
            return
        codec_box = codec_entry[1]
        ratio_spin = ratio_entry[1]
        explicit = saved not in (None, "", 0, 0.0)

        def apply(codec: str, force: bool):
            common = self._ratios.get(codec)
            if common is None:
                return
            current = float(ratio_spin.value())
            if force or current == 0 or (self._auto_ratio is not None and abs(current - self._auto_ratio) < 1e-6):
                self._auto_ratio = common
                ratio_spin.blockSignals(True)
                ratio_spin.setValue(common)
                ratio_spin.blockSignals(False)
            if self._ratio_hint is not None:
                self._ratio_hint.setText(t("ratio_common").format(codec=codec, ratio=common))

        codec_box.currentTextChanged.connect(lambda text: apply(text, False))
        apply(codec_box.currentText(), force=not explicit)

    def _browse_file(self, edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, t("browse"), "", "DBC (*.dbc)")
        if path:
            edit.setText(path)

    def _make_widget(self, field, current):
        kind = field["widget"]
        if kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(current) if current not in ("", None) else False)
            return box
        if kind == "format":
            bpp = None
            if isinstance(current, tuple):
                current, bpp = current
            return _format_editor(current, bpp)
        if kind == "enum":
            box = QComboBox()
            choices = _choices(field)
            box.addItems(choices)
            if current is not None and str(current) in choices:
                box.setCurrentText(str(current))
            return box
        if kind == "int":
            return _spin_int(current)
        if kind == "float":
            return _spin_float(current)
        edit = QLineEdit("" if current is None else str(current))
        return edit

    def _append_stage(self):
        self._add_stage_row("stage", "", self._stage_size[0], self._stage_size[1], 1.0, 1.0)
        self._fit_stages()

    def _add_stage_row(self, name, fmt, width, height, read_factor, write_factor, bpp=None):
        row = self.stages.rowCount()
        self.stages.insertRow(row)
        self.stages.setItem(row, 0, QTableWidgetItem(str(name)))
        self.stages.setCellWidget(row, 1, _format_editor(fmt, bpp, allow_blank=True))
        self.stages.setItem(row, 2, QTableWidgetItem("" if not width else str(width)))
        self.stages.setItem(row, 3, QTableWidgetItem("" if not height else str(height)))
        self.stages.setItem(row, 4, QTableWidgetItem(str(read_factor)))
        self.stages.setItem(row, 5, QTableWidgetItem(str(write_factor)))

    def _fit_stages(self):
        table = self.stages
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setCascadingSectionResizes(False)
        table.resizeColumnsToContents()
        table.resizeRowsToContents()
        height = header.height() + 8
        for row in range(table.rowCount()):
            height += table.rowHeight(row)
        table.setMinimumHeight(height)
        table.setMaximumHeight(16777215)
        table.setMinimumWidth(0)
        table.setMaximumWidth(16777215)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def result_values(self) -> dict:
        params = {}
        mode = None
        for key, (field, widget) in self._widgets.items():
            if field["widget"] == "format":
                value = widget._format_box.currentText()
                if value == CUSTOM_FORMAT:
                    params["bpp"] = float(widget._bpp_spin.value())
                else:
                    derived = bpp_for_format(value)
                    if derived is not None:
                        params["bpp"] = derived
            elif field["widget"] == "enum":
                value = widget.currentText()
            elif field["widget"] == "bool":
                value = bool(widget.isChecked())
            elif field["widget"] == "int":
                value = int(widget.value())
            elif field["widget"] == "float":
                value = float(widget.value())
            else:
                value = widget.text()
            if field.get("optional") and value in ("", 0, 0.0):
                continue
            if field.get("target") == "mode":
                mode = value
            else:
                params[key] = value
        stages = None
        if hasattr(self, "stages"):
            stages = []
            for row in range(self.stages.rowCount()):
                editor = self.stages.cellWidget(row, 1)
                fmt = editor._format_box.currentText().strip()
                width = self.stages.item(row, 2).text().strip()
                height = self.stages.item(row, 3).text().strip()
                stage = {
                    "name": self.stages.item(row, 0).text(),
                    "read_factor": float(self.stages.item(row, 4).text() or 1),
                    "write_factor": float(self.stages.item(row, 5).text() or 1),
                }
                if fmt:
                    stage["format"] = fmt
                if fmt == CUSTOM_FORMAT:
                    stage["bpp"] = float(editor._bpp_spin.value())
                if width:
                    stage["width"] = int(float(width))
                if height:
                    stage["height"] = int(float(height))
                stages.append(stage)
        chip = None
        if hasattr(self, "_chip_rate") and not self._chip_locked:
            rate = float(self._chip_rate.value())
            if rate > 0:
                chip = {"name": self.name_edit.text().strip(), "mpix_s": rate}
        return {
            "name": self.name_edit.text().strip(),
            "enabled": self._keep_enabled,
            "params": params,
            "mode": mode,
            "stages": stages,
            "chip": chip,
        }

    def _add_chip_row(self, form, topology):
        slots = {slot.name: slot.mpix_s for slot in (topology.isp_vpacs if topology is not None else [])}
        rate = slots.get(self.name_edit.text().strip())
        self._chip_locked = rate is not None
        self._chip_rate = _spin_float(rate or 0, 0, 100000)
        self._chip_rate.setDecimals(0)
        self._chip_rate.setSuffix(" MP/s")
        tip = t("isp_chip_tip")
        if self._chip_locked:
            _lock_widget(self._chip_rate)
        self._bind_tip(self._chip_rate, tip)
        label = QLabel(t("isp_chip"))
        label.setToolTip(tip)
        form.addRow(label, self._chip_rate)


_DDR_TYPES = ["", "LPDDR4", "LPDDR4X", "LPDDR5", "DDR4", "DDR5"]


def _ddr_type_box(current: str | None) -> QComboBox:
    box = QComboBox()
    box.setEditable(True)
    box.addItems(_DDR_TYPES)
    if current:
        box.setCurrentText(str(current))
    return box


def _lock_widget(widget):
    from PySide6.QtWidgets import QAbstractSpinBox
    widget.setStyleSheet("color: #4a4a4a; background-color: #e6e6e6;")
    if isinstance(widget, QAbstractSpinBox):
        widget.setReadOnly(True)
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    else:
        widget.setEnabled(False)


def _mbps_text(value: float) -> str:
    return f"{value:,.0f} MB/s ({value / 1000:.1f} GB/s)"


def _plain(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _rate_formula(mt_s: float, width: int | None, groups: int) -> str:
    bits = width or 32
    count = groups or 1
    formula = f"{mt_s:.0f} × {bits} / 8"
    if count > 1:
        formula += f" × {count}"
    return formula


def _side_peak(mt_s: float, width: int | None, groups: int) -> float:
    return round(mt_s * (width or 32) / 8.0 * (groups or 1), 2)


def _side_line(mt_s: float | None, width: int | None, groups: int, filled: str, empty: str) -> str:
    if mt_s is None:
        return t(empty)
    return t(filled).format(
        formula=_rate_formula(mt_s, width, groups),
        mbps=_mbps_text(_side_peak(mt_s, width, groups)),
    )


def ddr_conclusion_parts(channel) -> tuple[str, str, str]:
    """Controller peak, module peak, then the effective result. Same numbers as the engine."""
    from ..engine.margin import _compute_peaks

    ctrl = _side_line(
        channel.controller_mt_s,
        channel.controller_width_bits,
        channel.controller_groups,
        "ddr_sum_ctrl",
        "ddr_sum_ctrl_empty",
    )
    mod = _side_line(
        channel.module_mt_s,
        channel.module_width_bits,
        channel.module_groups,
        "ddr_sum_mod",
        "ddr_sum_mod_empty",
    )
    ctrl_peak, mod_peak, eff_peak, bottleneck = _compute_peaks(channel)
    if channel.controller_mt_s is not None and channel.module_mt_s is not None:
        available = round(eff_peak * channel.efficiency, 2)
        effective = "\n".join([
            t("ddr_sum_eff").format(
                left=f"{ctrl_peak:,.0f}",
                right=f"{mod_peak:,.0f}",
                mbps=_mbps_text(eff_peak),
            ),
            t("ddr_sum_bottle").format(name=t(f"ddr_bottle_{bottleneck}")),
            t("ddr_sum_avail").format(
                peak=f"{eff_peak:,.0f}",
                eff=_plain(channel.efficiency),
                mbps=_mbps_text(available),
            ),
        ])
        return ctrl, mod, effective
    return ctrl, mod, t("ddr_sum_empty")


def ddr_conclusion_text(channel) -> str:
    return "\n".join(ddr_conclusion_parts(channel))


def _none_if_zero(value: float) -> float | None:
    return None if value == 0 else float(value)


class DdrDialog(QDialog):
    def __init__(self, topology, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("ddr_title"))
        self.setMinimumWidth(720)
        self.topology = topology
        channel = topology.ddr_channels[0]
        form = QFormLayout(self)
        self.name = QLineEdit(channel.name)
        self._row(form, t("name"), self.name, t("name_tip"))
        form.addRow(self._section(t("ddr_ctrl")))
        self.ctrl_mts = _spin_float(channel.controller_mt_s or 0, 0, 20000)
        self.ctrl_width = _spin_int(channel.controller_width_bits or 0)
        self.ctrl_groups = _spin_int(channel.controller_groups or 1)
        self.ctrl_type = _ddr_type_box(channel.controller_type)
        # A preset already carries the datasheet spec, so those fields stay fixed.
        # A new SoC has none yet; the user has to type it.
        self._ctrl_locked = channel.controller_mt_s is not None
        if self._ctrl_locked:
            for widget in (self.ctrl_mts, self.ctrl_width, self.ctrl_groups, self.ctrl_type):
                _lock_widget(widget)
        self._row(form, t("ddr_ctrl_mts"), self.ctrl_mts, t("ddr_ctrl_mts_tip"))
        self._row(form, t("ddr_ctrl_width"), self.ctrl_width, t("ddr_ctrl_width_tip"))
        self._row(form, t("ddr_ctrl_groups"), self.ctrl_groups, t("ddr_ctrl_groups_tip"))
        self._row(form, t("ddr_ctrl_type"), self.ctrl_type, t("ddr_ctrl_type_tip"))
        self.ctrl_summary = self._note()
        form.addRow(self.ctrl_summary)
        form.addRow(self._section(t("ddr_mod")))
        self.mod_mts = _spin_float(channel.module_mt_s or 0, 0, 20000)
        self.mod_width = _spin_int(channel.module_width_bits or 0)
        self.mod_groups = _spin_int(channel.module_groups or 1)
        self.mod_type = _ddr_type_box(channel.module_type)
        self._row(form, t("ddr_mod_mts"), self.mod_mts, t("ddr_mod_mts_tip"))
        self._row(form, t("ddr_mod_width"), self.mod_width, t("ddr_mod_width_tip"))
        self._row(form, t("ddr_mod_groups"), self.mod_groups, t("ddr_mod_groups_tip"))
        self._row(form, t("ddr_mod_type"), self.mod_type, t("ddr_mod_type_tip"))
        self.mod_summary = self._note()
        form.addRow(self.mod_summary)
        form.addRow(self._section(t("ddr_effective")))
        self.efficiency = _spin_float(channel.efficiency, 0, 1)
        self.yellow = _spin_float(topology.alert_thresholds.get("yellow", 0.6), 0, 1)
        self.red = _spin_float(topology.alert_thresholds.get("red", 0.8), 0, 1)
        self._row(form, t("ddr_efficiency"), self.efficiency, t("ddr_eff_tip"))
        self.eff_summary = self._note()
        form.addRow(self.eff_summary)
        self._row(form, t("ddr_yellow"), self.yellow, "")
        self._row(form, t("ddr_red"), self.red, "")
        for widget in (
            self.ctrl_mts, self.ctrl_width, self.ctrl_groups,
            self.mod_mts, self.mod_width, self.mod_groups, self.efficiency,
        ):
            widget.valueChanged.connect(self._refresh_summary)
        self._refresh_summary()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        _label_box(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600; margin-top: 6px;")
        return label

    def _note(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            "background: #f4e4c4; color: #3d2e12; padding: 8px; border: 1px solid #b8893a;"
        )
        return label

    def _row(self, form: QFormLayout, label: str, widget, tip: str):
        if tip:
            widget.setToolTip(tip)
        name = QLabel(label)
        if tip:
            name.setToolTip(tip)
        form.addRow(name, widget)

    def _preview_channel(self):
        channel = self.topology.ddr_channels[0].model_copy(deep=True)
        channel.controller_mt_s = _none_if_zero(float(self.ctrl_mts.value()))
        ctrl_width = _none_if_zero(float(self.ctrl_width.value()))
        channel.controller_width_bits = int(ctrl_width) if ctrl_width is not None else None
        channel.controller_groups = max(1, int(self.ctrl_groups.value()))
        channel.module_mt_s = _none_if_zero(float(self.mod_mts.value()))
        width = _none_if_zero(float(self.mod_width.value()))
        channel.module_width_bits = int(width) if width is not None else None
        channel.module_groups = max(1, int(self.mod_groups.value()))
        channel.efficiency = float(self.efficiency.value())
        return channel

    def _refresh_summary(self):
        ctrl, mod, effective = ddr_conclusion_parts(self._preview_channel())
        self.ctrl_summary.setText(ctrl)
        self.mod_summary.setText(mod)
        self.eff_summary.setText(effective)

    def apply(self) -> None:
        channel = self.topology.ddr_channels[0]
        channel.name = self.name.text().strip() or channel.name
        if not self._ctrl_locked:
            channel.controller_mt_s = _none_if_zero(float(self.ctrl_mts.value()))
            channel.controller_width_bits = _none_if_zero(float(self.ctrl_width.value()))
            if channel.controller_width_bits is not None:
                channel.controller_width_bits = int(channel.controller_width_bits)
            channel.controller_groups = max(1, int(self.ctrl_groups.value()))
            channel.controller_type = self.ctrl_type.currentText().strip() or None
        channel.module_mt_s = _none_if_zero(float(self.mod_mts.value()))
        channel.module_width_bits = _none_if_zero(float(self.mod_width.value()))
        if channel.module_width_bits is not None:
            channel.module_width_bits = int(channel.module_width_bits)
        channel.module_groups = max(1, int(self.mod_groups.value()))
        channel.module_type = self.mod_type.currentText().strip() or None
        channel.efficiency = float(self.efficiency.value())
        self.topology.alert_thresholds = {
            "yellow": float(self.yellow.value()),
            "red": float(self.red.value()),
        }


class CanDialog(QDialog):
    def __init__(self, parent=None, node=None):
        super().__init__(parent)
        self.setWindowTitle(t("can_report"))
        self.resize(720, 520)
        self._node = node
        self._adjusting = False
        layout = QVBoxLayout(self)
        self._form = QFormLayout()
        form = self._form
        if node is not None:
            self.name_edit = QLineEdit(node.name)
            self.name_edit.setToolTip(t("name_tip"))
            form.addRow(t("name"), self.name_edit)
            self.mode = QComboBox()
            self.mode.addItem(t("can_mode_file"), "file")
            self.mode.addItem(t("can_mode_generic"), "generic")
            self.mode.setToolTip(t("can_tip_mode"))
            mode_label = QLabel(t("can_mode"))
            mode_label.setToolTip(t("can_tip_mode"))
            form.addRow(mode_label, self.mode)
            self.direction = QComboBox()
            self.direction.addItem(t("can_dir_both"), "both")
            self.direction.addItem(t("can_dir_tx"), "tx")
            self.direction.addItem(t("can_dir_rx"), "rx")
            self.direction.setToolTip(t("can_tip_direction"))
            direction_label = QLabel(t("can_direction"))
            direction_label.setToolTip(t("can_tip_direction"))
            form.addRow(direction_label, self.direction)

        self.file_row = QWidget()
        file_layout = QHBoxLayout(self.file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit()
        browse = QPushButton(t("browse"))
        browse.clicked.connect(self._browse)
        file_layout.addWidget(self.path, 1)
        file_layout.addWidget(browse)
        self.file_label = QLabel("DBC")
        form.addRow(self.file_label, self.file_row)

        self.standard = QComboBox()
        self.standard.addItem(t("can_std_fd"), "canfd")
        self.standard.addItem(t("can_std_can"), "can")
        form.addRow(t("can_standard"), self.standard)
        self.arbitration = QSpinBox()
        self.arbitration.setRange(1, 1000)
        self.arbitration.setValue(500)
        self.arb_row = _rate_row(self.arbitration, 1000)
        form.addRow(t("can_arb"), self.arb_row)
        self.data_rate = QSpinBox()
        self.data_rate.setRange(1, 8000)
        self.data_rate.setValue(2000)
        self.rate_label = QLabel(t("can_data"))
        self.rate_row = _rate_row(self.data_rate, 8000)
        form.addRow(self.rate_label, self.rate_row)
        self.load = QDoubleSpinBox()
        self.load.setRange(0, 100)
        self.load.setDecimals(1)
        self.load.setValue(30)
        self.load.setToolTip(t("can_tip_load"))
        self.load_label = QLabel(t("can_load_pct"))
        self.load_label.setToolTip(t("can_tip_load"))
        form.addRow(self.load_label, self.load)
        layout.addLayout(form)

        self.run_button = QPushButton(t("make_report"))
        self.run_button.clicked.connect(self._run)
        layout.addWidget(self.run_button)

        self.conclusion = QLabel("")
        self.conclusion.setWordWrap(True)
        self.conclusion.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        conclusion_font = QFont()
        conclusion_font.setPointSize(12)
        self.conclusion.setFont(conclusion_font)
        layout.addWidget(self.conclusion)
        self.step_labels = []
        for tip_key in ("can_tip_lat_bits", "can_tip_lat_tx", "can_tip_lat_load", "can_tip_lat_wait", "can_tip_lat_total"):
            label = QLabel("")
            label.setFont(_mono_font())
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setToolTip(t(tip_key))
            self.step_labels.append(label)
            layout.addWidget(label)

        self.messages = QTableWidget(0, 6)
        self.messages.setHorizontalHeaderLabels([t("message"), "ID", "DLC", t("cycle_ms"), "bps", t("share_pct")])
        _adapt_table(self.messages)
        layout.addWidget(self.messages)
        if node is not None:
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            box.accepted.connect(self.accept)
            box.rejected.connect(self.reject)
            layout.addWidget(box)
            self._load_node()

        self._sync_mode()
        self.standard.currentIndexChanged.connect(self._on_standard)
        self.arbitration.valueChanged.connect(self._on_arbitration)
        self.data_rate.valueChanged.connect(self._on_data)
        if node is not None:
            self.mode.currentIndexChanged.connect(self._on_mode)

    def _file_mode(self) -> bool:
        if self._node is None:
            return True
        return self.mode.currentData() == "file"

    def _load_node(self):
        params = dict(self._node.params or {})
        path = str(params.get("dbc_path") or "").strip()
        kbps = float(params.get("data_kbps") or 0)
        if kbps <= 0:
            kbps = float(params.get("bitrate_mbps") or 0.5) * 1000
        standard = params.get("standard")
        if standard not in ("can", "canfd"):
            standard = "canfd" if kbps > 1000 else "can"
        self._adjusting = True
        self.mode.setCurrentIndex(self.mode.findData("file" if path else "generic"))
        self.path.setText(path)
        self.standard.setCurrentIndex(max(0, self.standard.findData(standard)))
        self._apply_standard_limits()
        if standard == "canfd":
            arbitration = int(params.get("arbitration_kbps") or 500)
            self.arbitration.setValue(min(arbitration, 1000))
            if self.data_rate.value() < self.arbitration.value():
                self.data_rate.setValue(self.arbitration.value())
            self.data_rate.setValue(min(max(int(kbps), self.arbitration.value()), 8000))
        else:
            self.data_rate.setValue(min(max(int(kbps), 1), 1000))
        self.load.setValue(float(params.get("load_pct", 0.3)) * 100)
        direction = self.direction.findData(params.get("direction") or "both")
        if direction >= 0:
            self.direction.setCurrentIndex(direction)
        self._adjusting = False

    def _sync_mode(self):
        file_mode = self._file_mode()
        self.file_label.setVisible(file_mode)
        self.file_row.setVisible(file_mode)
        self.run_button.setVisible(file_mode)
        self.conclusion.setVisible(file_mode)
        for label in self.step_labels:
            label.setVisible(file_mode)
        self.messages.setVisible(file_mode)
        generic = self._node is not None and not file_mode
        self.load_label.setVisible(generic)
        self.load.setVisible(generic)

    def _on_mode(self):
        self._clear_result()
        self._sync_mode()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "DBC", "", "DBC (*.dbc)")
        if path:
            self.path.setText(path)
            self._clear_result()

    def _apply_standard_limits(self):
        fd = self.standard.currentData() == "canfd"
        self.arb_row.setVisible(fd)
        arb_label = self._form.labelForField(self.arb_row)
        if arb_label is not None:
            arb_label.setVisible(fd)
        self.rate_label.setText(t("can_data") if fd else t("can_rate"))
        self.data_rate.setMaximum(8000 if fd else 1000)
        if not fd and self.data_rate.value() > 1000:
            self.data_rate.setValue(1000)
        cap = self.rate_row.findChild(QLabel)
        if cap is not None:
            cap.setText(t("can_max").format(n=8000 if fd else 1000))
        if fd and self.data_rate.value() < self.arbitration.value():
            self.data_rate.setValue(self.arbitration.value())

    def _on_standard(self):
        self._adjusting = True
        self._apply_standard_limits()
        self._adjusting = False
        self._clear_result()

    def _on_arbitration(self):
        if self._adjusting or self.standard.currentData() != "canfd":
            return
        self._adjusting = True
        if self.data_rate.value() < self.arbitration.value():
            self.data_rate.setValue(self.arbitration.value())
        self._adjusting = False
        self._clear_result()

    def _on_data(self):
        if self._adjusting:
            return
        if self.standard.currentData() == "canfd" and self.arbitration.value() > self.data_rate.value():
            self._adjusting = True
            self.arbitration.setValue(self.data_rate.value())
            self._adjusting = False
        self._clear_result()

    def _clear_result(self):
        if self._adjusting:
            return
        self.conclusion.setText("")
        self.conclusion.setStyleSheet("")
        for label in self.step_labels:
            label.setText("")
            label.setStyleSheet("")
        self.messages.setRowCount(0)

    def _run(self):
        try:
            report = can_health(
                self.path.text().strip(),
                standard=self.standard.currentData(),
                arbitration_kbps=float(self.arbitration.value()),
                data_kbps=float(self.data_rate.value()),
            )
        except Exception as exc:
            self._show_conclusion(str(exc), [], "bad")
            self.messages.setRowCount(0)
            return
        self.messages.setRowCount(0)
        if not report.buses:
            self._clear_result()
            return
        bus = report.buses[0]
        for msg in bus.top_messages:
            mrow = self.messages.rowCount()
            self.messages.insertRow(mrow)
            cells = [
                str(msg.get("name", "")),
                str(msg.get("id", "")),
                str(msg.get("dlc", "")),
                str(msg.get("cycle_ms", "")),
                str(msg.get("bps", "")),
                str(msg.get("share_pct", "")),
            ]
            for col, value in enumerate(cells):
                self.messages.setItem(mrow, col, QTableWidgetItem(value))
        _adapt_table(self.messages)
        headline, steps = _conclusion_text(bus)
        self._show_conclusion(headline, steps, _conclusion_level(bus))

    def _show_conclusion(self, headline: str, steps: list[str], level: str):
        color = f"color: {_CAN_TEXT[level]};"
        self.conclusion.setText(headline)
        self.conclusion.setStyleSheet(color)
        for index, label in enumerate(self.step_labels):
            line = steps[index] if index < len(steps) else ""
            if line and index in (2, 4):
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setText(_ends_html(line, _CAN_TEXT[level]))
            else:
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setText(line)
            label.setStyleSheet("")

    def node_values(self) -> dict:
        standard = self.standard.currentData()
        data = int(self.data_rate.value())
        arbitration = int(self.arbitration.value()) if standard == "canfd" else data
        params = {
            "direction": self.direction.currentData(),
            "standard": standard,
            "arbitration_kbps": arbitration,
            "data_kbps": data,
            "bitrate_mbps": data / 1000.0,
        }
        if self._file_mode():
            path = self.path.text().strip()
            if path:
                params["dbc_path"] = path
        else:
            params["load_pct"] = float(self.load.value()) / 100.0
        return {
            "name": self.name_edit.text().strip() or self._node.name,
            "enabled": bool(self._node.enabled),
            "params": params,
            "mode": None,
            "stages": None,
        }


_CAN_TEXT = {"ok": "#1F8A4C", "warn": "#A16207", "bad": "#DC2626"}


def _ends_html(line: str, color: str) -> str:
    """Color only the leading name and the trailing result. The working stays plain."""
    left, rest = line.split(" = ", 1)
    middle, right = rest.rsplit(" = ", 1)
    return (
        f'<span style="color:{color}">{escape(left)} =</span>'
        f" {escape(middle)} "
        f'<span style="color:{color}">= {escape(right)}</span>'
    )


def _rate_row(spin: QSpinBox, maximum: int) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    spin.setMaximumWidth(120)
    layout.addWidget(spin)
    layout.addWidget(QLabel(t("can_max").format(n=maximum)))
    layout.addStretch(1)
    return row


def _conclusion_text(bus) -> tuple[str, list[str]]:
    if bus.illegal_count:
        key = "can_fit_can" if bus.standard == "can" else "can_fit_fd"
        return t(key).format(n=bus.illegal_count), []
    headline = t("can_result").format(
        load=f"{bus.load_pct * 100:.3f}",
        latency=f"{bus.worst_case_latency_ms:.2f}",
    )
    longest = max((int(msg["dlc"]) for msg in bus.top_messages), default=0)
    return headline, latency_text(
        longest, bus.data_kbps, bus.load_pct, bus.total_kbps, standard=bus.standard,
    )


def _conclusion_level(bus) -> str:
    if bus.illegal_count or bus.verdict == "CRITICAL":
        return "bad"
    if bus.verdict == "WARN":
        return "warn"
    return "ok"


def _mono_font() -> QFont:
    font = QFont("DejaVu Sans Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(10)
    return font


def _paint_tiers(boxes, recommendations, best: str) -> None:
    for box, row in zip(boxes, recommendations):
        box.show_tier(
            row["title"],
            float(row["capacity_mbps"]),
            float(row["util"]),
            row["tier"] == best,
        )


def _form_label(text_key: str, tip_key: str) -> QLabel:
    label = QLabel(t(text_key))
    label.setToolTip(t(tip_key))
    return label


def _encoding_editor() -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    box = QComboBox()
    box.addItem("9b/10b", "9b10b")
    box.addItem("8b/10b", "8b10b")
    box.addItem("none", "none")
    hint = QLabel(t("gmsl_enc_9b10b"))
    box.setToolTip(t("gmsl_tip_encoding"))
    hint.setToolTip(t("gmsl_tip_encoding"))

    def refresh():
        hint.setText(t(f"gmsl_enc_{box.currentData()}"))

    box.currentIndexChanged.connect(lambda _index: refresh())
    layout.addWidget(box)
    layout.addWidget(hint, 1)
    row._box = box  # type: ignore[attr-defined]
    return row


def _check_hint(tip_key: str, hint_key: str, checked: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    box = QCheckBox()
    box.setChecked(checked)
    hint = QLabel(t(hint_key))
    hint.setWordWrap(True)
    box.setToolTip(t(tip_key))
    hint.setToolTip(t(tip_key))
    layout.addWidget(box)
    layout.addWidget(hint, 1)
    row._box = box  # type: ignore[attr-defined]
    return row


def _phy_editor() -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    dphy = QRadioButton(t("gmsl_dphy"))
    cphy = QRadioButton(t("gmsl_cphy"))
    dphy.setChecked(True)
    dphy.setToolTip(t("gmsl_tip_dphy"))
    cphy.setToolTip(t("gmsl_tip_cphy"))
    layout.addWidget(dphy)
    layout.addWidget(cphy)
    layout.addStretch(1)
    row._dphy = dphy  # type: ignore[attr-defined]
    row._cphy = cphy  # type: ignore[attr-defined]
    return row


class _GmslTierBox(QFrame):
    """One GMSL tier. The whole card is green, yellow, or red after a calculation."""

    def __init__(self):
        super().__init__()
        self.setObjectName("gmslTier")
        self.setMinimumWidth(150)
        self.setMinimumHeight(136)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 14)
        self.name = QLabel("")
        self.capacity = QLabel("")
        self.occupancy = QLabel("")
        self.fit = QLabel("")
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(13)
        self.name.setFont(name_font)
        occ_font = QFont()
        occ_font.setPointSize(16)
        self.occupancy.setFont(occ_font)
        for label in (self.name, self.capacity, self.occupancy, self.fit):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        self.set_idle()

    def set_idle(self):
        self.name.setText("")
        self.capacity.setText("")
        self.occupancy.setText("")
        self.fit.setText("")
        self._paint((244, 247, 251))

    def show_tier(self, title: str, capacity_mbps: float, util: float, best: bool, note: str = ""):
        self.name.setText(title)
        self.capacity.setText(gbps_label(capacity_mbps))
        self.occupancy.setText(f"{util * 100:.1f}%")
        lines = []
        if note:
            lines.append(note)
        if best:
            lines.append(t("best_fit"))
        self.fit.setText("\n".join(lines))
        self._paint(ddr_level_rgb(util, 0.6, 0.8))

    def _paint(self, rgb: tuple[int, int, int]):
        red, green, blue = rgb
        self.setStyleSheet(
            "#gmslTier {"
            f"background-color: rgb({red}, {green}, {blue});"
            "border-radius: 8px;"
            "}"
            "#gmslTier QLabel { color: #1a1a1a; background: transparent; }"
        )


def _whole_number(value: float):
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


class GmslDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("gmsl_link"))
        self.resize(1040, 900)
        self._multi_path: str | None = None
        self._multi_csi_mbps: float | None = None
        self._multi_fill = False
        title_font = QFont()
        title_font.setBold(True)
        link_font = QFont()
        link_font.setPointSize(22)
        link_font.setBold(True)
        multi = QWidget()
        multi_layout = QVBoxLayout(multi)
        multi_form = QFormLayout()
        self.multi_blanking = _spin_float(1.2, 0)
        self.multi_blanking.setToolTip(t("gmsl_tip_blanking"))
        self.multi_heartbeat = QCheckBox()
        self.multi_heartbeat.setToolTip(t("gmsl_tip_heartbeat"))
        self.multi_encoding_row = _encoding_editor()
        self.multi_encoding = self.multi_encoding_row._box
        self.multi_crc_row = _check_hint("gmsl_tip_crc", "gmsl_crc_hint", True)
        self.multi_crc = self.multi_crc_row._box
        self.multi_fec_row = _check_hint("gmsl_tip_fec", "gmsl_fec_hint", False)
        self.multi_fec = self.multi_fec_row._box
        self.multi_phy_row = _phy_editor()
        self.multi_dphy = self.multi_phy_row._dphy
        self.multi_cphy = self.multi_phy_row._cphy
        self.multi_lanes = QSpinBox()
        self.multi_lanes.setRange(1, 4)
        self.multi_lanes.setValue(4)
        self.multi_lanes.setToolTip(t("gmsl_tip_lanes"))
        self.multi_lane_label = _form_label("gmsl_lane", "gmsl_tip_lanes")
        multi_form.addRow(_form_label("gmsl_blanking", "gmsl_tip_blanking"), self.multi_blanking)
        multi_form.addRow(_form_label("gmsl_heartbeat", "gmsl_tip_heartbeat"), self.multi_heartbeat)
        multi_form.addRow(_form_label("gmsl_encoding", "gmsl_tip_encoding"), self.multi_encoding_row)
        multi_form.addRow(_form_label("gmsl_crc", "gmsl_tip_crc"), self.multi_crc_row)
        multi_form.addRow(_form_label("gmsl_fec", "gmsl_tip_fec"), self.multi_fec_row)
        multi_layout.addLayout(multi_form)
        self.multi_table = QTableWidget(0, 5)
        self.multi_table.setHorizontalHeaderLabels([
            t("name"), t("gmsl_width"), t("gmsl_height"), t("gmsl_fps"), t("gmsl_format"),
        ])
        self.multi_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.multi_table.verticalHeader().setVisible(False)
        self.multi_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.multi_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.multi_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for index, tip_key in ((1, "gmsl_tip_width"), (2, "gmsl_tip_height"), (3, "gmsl_tip_fps"), (4, "gmsl_tip_format")):
            item = self.multi_table.horizontalHeaderItem(index)
            if item is not None:
                item.setToolTip(t(tip_key))
        multi_layout.addWidget(self.multi_table)
        buttons = QHBoxLayout()
        add_btn = QPushButton(t("gmsl_add"))
        add_btn.clicked.connect(lambda: self._add_link_row())
        self.multi_add = add_btn
        remove_btn = QPushButton(t("delete"))
        remove_btn.clicked.connect(self._remove_link_row)
        open_btn = QPushButton(t("gmsl_open"))
        open_btn.clicked.connect(self._open_yaml)
        save_btn = QPushButton(t("save"))
        save_btn.clicked.connect(self._save_multi)
        calc_btn = QPushButton(t("calculate"))
        calc_btn.clicked.connect(self._calculate_multi)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch()
        buttons.addWidget(open_btn)
        buttons.addWidget(save_btn)
        buttons.addWidget(calc_btn)
        multi_layout.addLayout(buttons)
        self.multi_breakdown_title = QLabel(t("gmsl_breakdown"))
        self.multi_breakdown_title.setFont(title_font)
        self.multi_breakdown_title.setToolTip(t("gmsl_tip_packet"))
        self.multi_steps = QLabel("")
        self.multi_steps.setFont(_mono_font())
        self.multi_steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.multi_steps.setToolTip(t("gmsl_tip_packet"))
        self.multi_link_title = QLabel(t("gmsl_link_bw"))
        self.multi_link_title.setFont(title_font)
        self.multi_link_bw = QLabel("")
        self.multi_link_bw.setFont(link_font)
        multi_layout.addWidget(self.multi_breakdown_title)
        multi_layout.addWidget(self.multi_steps)
        multi_layout.addWidget(self.multi_link_title)
        multi_layout.addWidget(self.multi_link_bw)
        multi_boxes = QHBoxLayout()
        self.multi_tiers = [_GmslTierBox() for _ in range(4)]
        for box in self.multi_tiers:
            multi_boxes.addWidget(box)
        multi_layout.addLayout(multi_boxes)
        multi_layout.addSpacing(16)
        multi_csi = QLabel(t("gmsl_csi"))
        multi_csi.setFont(title_font)
        multi_layout.addWidget(multi_csi)
        multi_csi_form = QFormLayout()
        multi_csi_form.addRow(_form_label("gmsl_phy", "gmsl_tip_dphy"), self.multi_phy_row)
        multi_csi_form.addRow(self.multi_lane_label, self.multi_lanes)
        multi_layout.addLayout(multi_csi_form)
        self.multi_csi_lines = QLabel("")
        self.multi_csi_lines.setFont(_mono_font())
        self.multi_csi_lines.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.multi_csi_lines.setToolTip(t("gmsl_tip_lanes"))
        multi_layout.addWidget(self.multi_csi_lines)
        multi_layout.addStretch()
        self._sync_phy(self.multi_dphy, self.multi_cphy, self.multi_lane_label, self.multi_lanes)
        self.multi_blanking.valueChanged.connect(self._live_multi)
        self.multi_heartbeat.toggled.connect(self._live_multi)
        self.multi_encoding.currentIndexChanged.connect(self._live_multi)
        self.multi_crc.toggled.connect(self._live_multi)
        self.multi_fec.toggled.connect(self._live_multi)
        self.multi_dphy.toggled.connect(self._on_multi_phy)
        self.multi_lanes.valueChanged.connect(self._refresh_multi_clock)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(multi)
        self._multi_fill = True
        self._add_link_row()
        self._multi_fill = False
        self._fit_camera_table()
        self._alias_page()
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

    def _alias_page(self):
        """One page. These names are the controls tests and callers already use."""
        self.blanking = self.multi_blanking
        self.heartbeat = self.multi_heartbeat
        self.encoding = self.multi_encoding
        self.crc = self.multi_crc
        self.fec = self.multi_fec
        self.dphy = self.multi_dphy
        self.cphy = self.multi_cphy
        self.lanes = self.multi_lanes
        self.steps = self.multi_steps
        self.link_bw = self.multi_link_bw
        self.tier_boxes = self.multi_tiers
        self.csi_lines = self.multi_csi_lines
        editor = self.multi_table.cellWidget(0, 4) if self.multi_table.rowCount() else None
        self.format_row = editor

    def _single(self):
        self._calculate_multi()

    def _fit_camera_table(self):
        """Keep the grid exactly four rows tall. Extra viewport space stays outside it."""
        table = self.multi_table
        header = table.horizontalHeader().height() or table.horizontalHeader().sizeHint().height()
        height = header + table.frameWidth() * 2 + MAX_STREAMS * 36
        table.setFixedHeight(height)
        self.multi_add.setEnabled(table.rowCount() < MAX_STREAMS)

    def _add_link_row(self, spec: dict | None = None):
        if self.multi_table.rowCount() >= MAX_STREAMS:
            return
        spec = spec or {
            "name": f"CAM{self.multi_table.rowCount() + 1}",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "format": "raw12",
        }
        fmt, custom_bpp = format_for_link(spec)
        row = self.multi_table.rowCount()
        self.multi_table.insertRow(row)
        name = QLineEdit(str(spec.get("name") or ""))
        width = _spin_int(int(spec.get("width") or 1920))
        height = _spin_int(int(spec.get("height") or 1080))
        fps = _spin_float(float(spec.get("fps") or 30), 0)
        editor = _format_editor(fmt, custom_bpp)
        for col, widget in enumerate((name, width, height, fps, editor)):
            self.multi_table.setCellWidget(row, col, widget)
        self.multi_table.setRowHeight(row, 36)
        name.textChanged.connect(self._clear_multi_result)
        width.valueChanged.connect(self._clear_multi_result)
        height.valueChanged.connect(self._clear_multi_result)
        fps.valueChanged.connect(self._clear_multi_result)
        editor._format_box.setToolTip(t("gmsl_tip_format"))
        editor._bpp_spin.setToolTip(t("gmsl_tip_format"))
        width.setToolTip(t("gmsl_tip_width"))
        height.setToolTip(t("gmsl_tip_height"))
        fps.setToolTip(t("gmsl_tip_fps"))
        editor._format_box.currentTextChanged.connect(self._clear_multi_result)
        editor._bpp_spin.valueChanged.connect(self._clear_multi_result)
        if not self._multi_fill:
            self._clear_multi_result()
        self._fit_camera_table()

    def _remove_link_row(self):
        row = self.multi_table.currentRow()
        if row < 0:
            row = self.multi_table.rowCount() - 1
        if row < 0:
            return
        self.multi_table.removeRow(row)
        self._fit_camera_table()
        self._clear_multi_result()

    def _link_spec(self, row: int) -> dict:
        name = self.multi_table.cellWidget(row, 0).text().strip() or f"LINK{row + 1}"
        editor = self.multi_table.cellWidget(row, 4)
        fmt = editor._format_box.currentText()
        spec = {
            "name": name,
            "width": int(self.multi_table.cellWidget(row, 1).value()),
            "height": int(self.multi_table.cellWidget(row, 2).value()),
            "fps": _whole_number(float(self.multi_table.cellWidget(row, 3).value())),
            "format": fmt,
        }
        if fmt == CUSTOM_FORMAT:
            spec["bpp"] = float(editor._bpp_spin.value())
        return spec

    def _clear_multi_result(self):
        if self._multi_fill:
            return
        self.multi_steps.setText("")
        self.multi_link_bw.setText("")
        for box in self.multi_tiers:
            box.set_idle()
        self._multi_csi_mbps = None
        self._refresh_multi_clock()

    def _multi_overrides(self) -> dict:
        return {
            "blanking": float(self.multi_blanking.value()),
            "heartbeat": self.multi_heartbeat.isChecked(),
            "encoding": self.multi_encoding.currentData(),
            "pixel_crc": self.multi_crc.isChecked(),
            "fec": self.multi_fec.isChecked(),
            "phy": "cphy" if self.multi_cphy.isChecked() else "dphy",
            "lanes": int(self.multi_lanes.value()),
        }

    def _live_multi(self):
        if self._multi_fill or self._multi_csi_mbps is None:
            return
        self._calculate_multi()

    def _on_multi_phy(self):
        self._sync_phy(self.multi_dphy, self.multi_cphy, self.multi_lane_label, self.multi_lanes)
        self._refresh_multi_clock()

    def _calculate_multi(self):
        from ..gmsl.calculator import build_report_from_links

        if self.multi_table.rowCount() == 0:
            self._clear_multi_result()
            return
        specs = [self._link_spec(row) for row in range(self.multi_table.rowCount())]
        report = build_report_from_links(specs, self._multi_overrides())
        coax = sum(link.link_bw_mbps for link in report.links)
        if len(report.links) == 1:
            self.multi_steps.setText(link_steps(report.links[0]))
        else:
            self.multi_steps.setText(multi_steps(report.links))
        rows, best = coax_tier_rows(report.links)
        self.multi_link_bw.setText(bandwidth_line(coax))
        _paint_tiers(self.multi_tiers, rows, best)
        self._multi_csi_mbps = sum(link.csi_mbps for link in report.links)
        self._refresh_multi_clock()

    def _refresh_multi_clock(self):
        self.multi_csi_lines.setText(csi_text(
            self._multi_csi_mbps,
            "cphy" if self.multi_cphy.isChecked() else "dphy",
            int(self.multi_lanes.value()),
        ))

    def _sync_phy(self, dphy, cphy, lane_label, lanes_spin):
        cphy_on = cphy.isChecked()
        lane_label.setText(t("gmsl_trio") if cphy_on else t("gmsl_lane"))
        lanes_spin.setMaximum(3 if cphy_on else 4)
        dphy.setToolTip(t("gmsl_tip_dphy"))

    def _load_multi(self, path: str):
        links, overrides = load_yaml(path)
        if len(links) > MAX_STREAMS:
            raise ValueError(t("gmsl_too_many"))
        options = link_options(overrides)
        self._multi_fill = True
        self.multi_table.setRowCount(0)
        if overrides.get("blanking") is not None:
            self.multi_blanking.setValue(float(overrides["blanking"]))
        self.multi_heartbeat.setChecked(options["heartbeat"])
        index = self.multi_encoding.findData(options["encoding"])
        if index >= 0:
            self.multi_encoding.setCurrentIndex(index)
        self.multi_crc.setChecked(options["pixel_crc"])
        self.multi_fec.setChecked(options["fec"])
        self.multi_cphy.setChecked(options["phy"] == "cphy")
        self.multi_dphy.setChecked(options["phy"] != "cphy")
        self.multi_lanes.setValue(options["lanes"])
        for spec in links:
            self._add_link_row(spec)
        self._multi_path = path
        self._multi_fill = False
        self._on_multi_phy()
        self._clear_multi_result()

    def _save_multi(self):
        path = self._multi_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, t("save"), "gmsl_links.yaml", "YAML (*.yaml *.yml)")
            if not path:
                return
            self._multi_path = path
        specs = [self._link_spec(row) for row in range(self.multi_table.rowCount())]
        options = self._multi_overrides()
        save_yaml(
            path,
            specs,
            options["blanking"],
            heartbeat=options["heartbeat"],
            encoding=options["encoding"],
            fec=options["fec"],
            pixel_crc=options["pixel_crc"],
            phy=options["phy"],
            lanes=options["lanes"],
        )

    def _open_yaml(self):
        path, _ = QFileDialog.getOpenFileName(self, t("open_gmsl"), "", "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            self._load_multi(path)
        except ValueError as exc:
            QMessageBox.warning(self, t("gmsl_link"), str(exc))
