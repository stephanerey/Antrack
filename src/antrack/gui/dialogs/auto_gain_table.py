"""Editable SDRplay-style auto gain table widgets for the SDR tab."""

from __future__ import annotations

from copy import deepcopy

from PyQt5 import QtCore, QtGui, QtWidgets

from antrack.tools.gain_table import (
    AUTO_GAIN_BAND_LABELS,
    AUTO_GAIN_LEVELS_DBM,
    DEFAULT_AUTO_GAIN_LEVEL_DBM,
    band_label_for_frequency,
    build_default_auto_gain_profiles,
    find_band_index,
    pair_text,
    parse_pair_text,
)


class AutoGainTableWidget(QtWidgets.QWidget):
    """Expose an editable per-band gain table and active profile selection."""

    activeLevelChanged = QtCore.pyqtSignal(int)
    profilesChanged = QtCore.pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._profiles = build_default_auto_gain_profiles()
        self._selected_level_dbm = int(DEFAULT_AUTO_GAIN_LEVEL_DBM)
        self._current_band_index = 0
        self._updating_table = False

        self.currentBandLabel = QtWidgets.QLabel(self)
        self.currentBandLabel.setObjectName("currentBandLabel")

        self.table = QtWidgets.QTableWidget(self)
        self.table.setObjectName("autoGainTable")
        self.table.setRowCount(len(AUTO_GAIN_LEVELS_DBM))
        self.table.setColumnCount(len(AUTO_GAIN_BAND_LABELS))
        self.table.setHorizontalHeaderLabels(AUTO_GAIN_BAND_LABELS)
        self.table.setVerticalHeaderLabels([str(level) for level in AUTO_GAIN_LEVELS_DBM])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)

        self.helpLabel = QtWidgets.QLabel(
            "Cell format: LNA state / IF attenuation. Selected row is the active auto profile.",
            self,
        )
        self.helpLabel.setWordWrap(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.currentBandLabel)
        layout.addWidget(self.table)
        layout.addWidget(self.helpLabel)

        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        self.table.cellChanged.connect(self._on_cell_changed)

        self._refresh_table_contents()
        self.set_current_frequency(137_000_000.0)
        self._select_level_row(self._selected_level_dbm)

    @property
    def selected_level_dbm(self) -> int:
        return int(self._selected_level_dbm)

    def get_profiles(self) -> dict[int, list[tuple[int, int]]]:
        return deepcopy(self._profiles)

    def get_active_pair_for_frequency(self, freq_hz: float) -> tuple[int, int]:
        band_index = find_band_index(freq_hz)
        return tuple(self._profiles[self._selected_level_dbm][band_index])

    def set_current_frequency(self, freq_hz: float) -> None:
        self._current_band_index = find_band_index(freq_hz)
        self.currentBandLabel.setText(
            f"Current band: {band_label_for_frequency(freq_hz)} MHz | Active level: {self._selected_level_dbm} dBm"
        )
        self._update_band_highlight()

    def _refresh_table_contents(self) -> None:
        self._updating_table = True
        try:
            for row, level_dbm in enumerate(AUTO_GAIN_LEVELS_DBM):
                pairs = self._profiles[int(level_dbm)]
                for col, pair in enumerate(pairs):
                    item = self.table.item(row, col)
                    if item is None:
                        item = QtWidgets.QTableWidgetItem()
                        item.setTextAlignment(QtCore.Qt.AlignCenter)
                        self.table.setItem(row, col, item)
                    item.setText(pair_text(pair))
                    item.setToolTip(
                        f"Level {level_dbm} dBm | Band {AUTO_GAIN_BAND_LABELS[col]} MHz | "
                        f"LNA state {pair[0]} | IF attenuation {pair[1]} dB"
                    )
        finally:
            self._updating_table = False
        self._update_band_highlight()

    def _select_level_row(self, level_dbm: int) -> None:
        row = AUTO_GAIN_LEVELS_DBM.index(int(level_dbm))
        self.table.setCurrentCell(row, self._current_band_index)
        self.table.selectRow(row)
        self._selected_level_dbm = int(level_dbm)
        self.currentBandLabel.setText(
            f"Current band: {AUTO_GAIN_BAND_LABELS[self._current_band_index]} MHz | "
            f"Active level: {self._selected_level_dbm} dBm"
        )

    def _update_band_highlight(self) -> None:
        header = self.table.horizontalHeader()
        for col, label in enumerate(AUTO_GAIN_BAND_LABELS):
            item = self.table.horizontalHeaderItem(col)
            if item is None:
                item = QtWidgets.QTableWidgetItem(label)
                self.table.setHorizontalHeaderItem(col, item)
            font = item.font()
            font.setBold(col == self._current_band_index)
            item.setFont(font)
            item.setBackground(QtGui.QColor("#cfe9ff") if col == self._current_band_index else QtGui.QBrush())
        header.viewport().update()

    def _on_current_cell_changed(self, row: int, _column: int, _prev_row: int, _prev_column: int) -> None:
        if row < 0 or row >= len(AUTO_GAIN_LEVELS_DBM):
            return
        level_dbm = int(AUTO_GAIN_LEVELS_DBM[row])
        if level_dbm == self._selected_level_dbm:
            return
        self._selected_level_dbm = level_dbm
        self.currentBandLabel.setText(
            f"Current band: {AUTO_GAIN_BAND_LABELS[self._current_band_index]} MHz | "
            f"Active level: {self._selected_level_dbm} dBm"
        )
        self.activeLevelChanged.emit(level_dbm)

    def _on_cell_changed(self, row: int, column: int) -> None:
        if self._updating_table:
            return
        item = self.table.item(row, column)
        if item is None:
            return
        parsed = parse_pair_text(item.text())
        if parsed is None:
            self._updating_table = True
            try:
                item.setText(pair_text(self._profiles[int(AUTO_GAIN_LEVELS_DBM[row])][column]))
            finally:
                self._updating_table = False
            return
        level_dbm = int(AUTO_GAIN_LEVELS_DBM[row])
        self._profiles[level_dbm][column] = (int(parsed[0]), int(parsed[1]))
        item.setToolTip(
            f"Level {level_dbm} dBm | Band {AUTO_GAIN_BAND_LABELS[column]} MHz | "
            f"LNA state {parsed[0]} | IF attenuation {parsed[1]} dB"
        )
        self.profilesChanged.emit(self.get_profiles())


class AutoGainTableDialog(QtWidgets.QDialog):
    """Modeless dialog wrapper for the auto-gain table widget."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Auto Gain Table")
        self.resize(900, 420)
        self.widget = AutoGainTableWidget(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.widget)
