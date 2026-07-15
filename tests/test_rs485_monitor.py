import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from antrack.core.axis.rs485_diagnostics import Rs485DiagnosticEvent, Rs485Result
from antrack.gui.diagnostics.rs485_monitor import (
    Rs485EventTableModel,
    Rs485FilterProxyModel,
    Rs485MonitorDialog,
    Rs485MonitorPalette,
    _initial_window_size,
)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _event(event_id: int, direction: str, *, result: str = "OK", decoded: str = "status"):
    return Rs485DiagnosticEvent(
        event_id=event_id,
        timestamp_wall=datetime.now().astimezone(),
        timestamp_monotonic_ns=event_id,
        direction=direction,
        axis="AZ",
        category="Status",
        function_code=3,
        transaction_id=event_id,
        raw_frame=b"\x0a\x03",
        decoded=decoded,
        result=result,
    )


def test_direction_labels_and_styles_are_explicit_and_distinct(app):
    model = Rs485EventTableModel()
    model.append_events((_event(1, "TX"), _event(2, "RX"), _event(3, "EVENT")))

    assert [model.data(model.index(row, 2), Qt.DisplayRole) for row in range(3)] == ["TX", "RX", "EVENT"]
    backgrounds = [model.data(model.index(row, 2), Qt.BackgroundRole).name() for row in range(3)]
    assert len(set(backgrounds)) == 3


def test_error_style_has_priority_over_tx_or_rx_style(app):
    model = Rs485EventTableModel()
    tx_ok = _event(1, "TX")
    tx_error = _event(2, "TX", result=Rs485Result.TIMEOUT.value)
    rx_error = _event(3, "RX", result=Rs485Result.CRC_ERROR.value)
    model.append_events((tx_ok, tx_error, rx_error))

    normal = model.data(model.index(0, 10), Qt.BackgroundRole)
    tx_error_color = model.data(model.index(1, 10), Qt.BackgroundRole)
    rx_error_color = model.data(model.index(2, 10), Qt.BackgroundRole)
    assert tx_error_color != normal
    assert tx_error_color == rx_error_color
    assert model.data(model.index(1, 2), Qt.DisplayRole) == "TX"
    assert model.data(model.index(2, 2), Qt.DisplayRole) == "RX"


def test_retry_uses_warning_style(app):
    event = _event(1, "EVENT", result=Rs485Result.RETRY.value)
    assert Rs485MonitorPalette.background(event) == Rs485MonitorPalette.colors()["warning"]


@pytest.mark.parametrize("base_color", (QColor("#ffffff"), QColor("#202020")))
def test_palette_is_readable_in_light_and_dark_theme(app, base_color):
    original = QPalette(app.palette())
    palette = QPalette(original)
    palette.setColor(QPalette.Base, base_color)
    app.setPalette(palette)
    try:
        tx = Rs485MonitorPalette.background(_event(1, "TX"))
        rx = Rs485MonitorPalette.background(_event(2, "RX"))
        event = Rs485MonitorPalette.background(_event(3, "EVENT"))
        foreground = Rs485MonitorPalette.foreground(_event(4, "TX"))
        assert len({tx.name(), rx.name(), event.name()}) == 3
        assert foreground.isValid()
        assert abs(foreground.lightness() - tx.lightness()) >= 40
    finally:
        app.setPalette(original)


def test_direction_filter_is_independent_from_coloring(app):
    model = Rs485EventTableModel()
    model.append_events((_event(1, "TX"), _event(2, "RX"), _event(3, "EVENT")))
    proxy = Rs485FilterProxyModel()
    proxy.setSourceModel(model)

    proxy.set_directions({"RX"})

    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 2), Qt.DisplayRole) == "RX"
    assert proxy.data(proxy.index(0, 2), Qt.BackgroundRole) == Rs485MonitorPalette.background(_event(9, "RX"))


def test_text_axis_category_and_error_filters(app):
    model = Rs485EventTableModel()
    model.append_events(
        (
            _event(1, "TX", decoded="read status"),
            _event(2, "RX", result=Rs485Result.CRC_ERROR.value, decoded="bad reply"),
        )
    )
    proxy = Rs485FilterProxyModel()
    proxy.setSourceModel(model)
    proxy.set_search_text("bad reply")
    assert proxy.rowCount() == 1

    proxy.set_search_text("")
    proxy.errors_only = True
    proxy.invalidateFilter()
    assert proxy.rowCount() == 1

    proxy.errors_only = False
    proxy.set_axis("EL")
    assert proxy.rowCount() == 0


def test_table_is_model_based_and_uses_no_cell_widgets(app):
    dialog = Rs485MonitorDialog()
    try:
        dialog.model.append_events((_event(100_001, "TX"),))
        assert isinstance(dialog.table.model(), Rs485FilterProxyModel)
        assert dialog.table.indexWidget(dialog.proxy.index(0, 0)) is None
        assert dialog.model.data(dialog.model.index(0, 0), Qt.BackgroundRole) is not None
    finally:
        dialog._bridge.stop()
        dialog._batch_timer.stop()
        dialog._stats_timer.stop()
        dialog.deleteLater()


def test_visible_model_buffer_is_bounded(app):
    model = Rs485EventTableModel(max_rows=2)
    model.append_events((_event(1, "TX"), _event(2, "RX"), _event(3, "EVENT")))
    assert model.rowCount() == 2
    assert model.event_at(0).event_id == 2


def test_visible_model_is_unbounded_by_default_and_limit_is_configurable(app):
    model = Rs485EventTableModel()
    model.append_events(tuple(_event(event_id, "RX") for event_id in range(1, 5)))

    assert model.max_rows is None
    assert [event.event_id for event in model.events()] == [1, 2, 3, 4]

    model.set_max_rows(2)
    assert [event.event_id for event in model.events()] == [3, 4]

    model.set_max_rows(None)
    model.append_events((_event(5, "RX"), _event(6, "RX"), _event(7, "RX")))
    assert [event.event_id for event in model.events()] == [3, 4, 5, 6, 7]


def test_dialog_retention_defaults_to_unlimited_session(app):
    dialog = Rs485MonitorDialog()
    try:
        assert dialog.retention_combo.currentData() is None
        assert dialog.model.max_rows is None
        assert dialog._pending.maxlen is None
    finally:
        dialog._bridge.stop()
        dialog._batch_timer.stop()
        dialog._stats_timer.stop()
        dialog.deleteLater()


def test_default_window_height_shows_the_full_diagnostics_column():
    large_screen = _initial_window_size(QSize(1920, 1400))
    smaller_screen = _initial_window_size(QSize(1366, 768))

    assert large_screen == QSize(1450, 1120)
    assert smaller_screen.width() <= round(1366 * 0.94)
    assert smaller_screen.height() <= round(768 * 0.94)
