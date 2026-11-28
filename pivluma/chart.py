"""Interactive price, moving average, and forecast chart."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from pivluma.appearance import colors_for
from pivluma.forecast import TRAINING_WINDOW
from pivluma.localization import translate


class PriceChart(QWidget):
    selected = Signal(object)
    viewport_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self.history = []
        self.days = []
        self.history_limit = 120
        self.history_offset = 0
        self.axis_limits = None
        self.selected_index = None
        self.plot_area = QRectF()
        self.colors = colors_for("dark-blue", Qt.ColorScheme.Dark)
        self.result = None
        self.llama_forecast = ()
        self.language = "en"
        self.layers = {"close": True, "average": False, "forecast": True, "interval": True, "llama": True, "llama_band": True}
        self.drag_start = None
        self.drag_offset = 0
        self.dragged = False
        self.hover_index = None
        self.setAccessibleName("Closing prices and forecast interval")

    def bounds(self):
        end = len(self.history) - self.history_offset
        return max(0, end - (self.history_limit or len(self.history))), end

    def visible_history(self):
        start, end = self.bounds()
        return self.history[start:end]

    def visible_days(self):
        start, end = self.bounds()
        return self.days[start:end]

    def forecast_visible(self):
        return self.history_offset == 0 and (
            self.layers["forecast"] or self.layers["interval"] or self.llama_visible() or self.llama_band_visible()
        )

    def llama_visible(self):
        return self.layers["llama"] and bool(self.llama_forecast)

    def llama_band_visible(self):
        return self.layers["llama_band"] and self.llama_visible()

    def llama_bounds(self, step):
        center = self.llama_forecast[step]
        baseline = self.result.forecast[step]
        return (max(0., center - (baseline - self.result.lower[step])),
                center + (self.result.upper[step] - baseline))

    def moving_average(self):
        start, end = self.bounds()
        return [(index - start, sum(self.history[index + 1 - TRAINING_WINDOW:index + 1]) / TRAINING_WINDOW)
                for index in range(max(start, TRAINING_WINDOW - 1), end)]

    def price_bounds(self):
        history = self.visible_history()
        values = list(history)
        if self.forecast_visible():
            if self.layers["forecast"]:
                values.extend(self.result.forecast)
            if self.layers["interval"]:
                values.extend(self.result.lower)
                values.extend(self.result.upper)
        if self.history_offset == 0 and self.llama_visible():
            values.extend(self.llama_forecast)
        if self.history_offset == 0 and self.llama_band_visible():
            for step in range(len(self.llama_forecast)):
                values.extend(self.llama_bounds(step))
        if self.layers["average"]:
            values.extend(value for _, value in self.moving_average())
        lower, upper = min(values), max(values)
        padding = (upper - lower) * .08 or max(upper * .01, .001)
        return lower - padding, upper + padding

    def set_history_limit(self, limit):
        self.history_limit = limit
        self.history_offset = 0
        self.view_changed()

    def reset_view(self):
        self.set_axis()
        self.set_history_limit(120)

    def set_layer(self, key, enabled):
        self.layers[key] = enabled
        self.view_changed()

    def view_changed(self):
        self.hover_index = None
        self.select_index(None)
        self.viewport_changed.emit()

    def pan_history(self, offset):
        limit = min(self.history_limit or len(self.history), len(self.history))
        offset = max(0, min(offset, len(self.history) - limit))
        if offset != self.history_offset:
            self.history_offset = offset
            self.view_changed()

    def zoom_history(self, steps):
        current = len(self.visible_history())
        limit = min(len(self.history), max(10, round(current * .75 ** steps)))
        if limit == current:
            return
        self.history_limit = limit
        self.history_offset = min(self.history_offset, len(self.history) - limit)
        self.view_changed()

    def set_axis(self, minimum=None, maximum=None):
        self.axis_limits = None if minimum is None else (minimum, maximum)
        self.update()

    def select_index(self, index):
        self.selected_index = index
        self.selected.emit(index)
        self.update()

    def point_text(self, index):
        history = self.visible_history()
        if index < len(history):
            template = translate(self.language, "{date} · Close {price:.8g}")
            return template.format(date=self.visible_days()[index], price=history[index])
        step = index - len(history)
        template = translate(
            self.language, "Forecast step {step} · {price:.8g} · Interval [{lower:.8g}, {upper:.8g}]"
        )
        text = template.format(step=step + 1, price=self.result.forecast[step],
                               lower=self.result.lower[step], upper=self.result.upper[step])
        if self.llama_visible():
            label = translate(self.language, "llama.cpp forecast")
            text += f" · {label} {self.llama_forecast[step]:.8g}"
            if self.llama_band_visible():
                lower, upper = self.llama_bounds(step)
                band = translate(self.language, "llama.cpp scenario band")
                text += f" · {band} [{lower:.8g}, {upper:.8g}]"
        return text

    def index_at(self, position):
        if not self.plot_area.contains(position) or self.plot_area.width() <= 0:
            return None
        count = len(self.visible_history()) + (len(self.result.forecast) if self.forecast_visible() else 0)
        fraction = (position.x() - self.plot_area.left()) / self.plot_area.width()
        return round(fraction * (count - 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.index_at(event.position()) is not None:
            self.drag_start = event.position().x()
            self.drag_offset = self.history_offset
            self.dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            distance = event.position().x() - self.drag_start
            if self.dragged or abs(distance) > 5:
                self.dragged = True
                distance_in_prices = round(distance * len(self.visible_history()) / max(1, self.plot_area.width()))
                self.pan_history(self.drag_offset + distance_in_prices)
            return
        index = self.index_at(event.position())
        if index != self.hover_index:
            self.hover_index = index
            if index is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPosition().toPoint(), self.point_text(index), self)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_start is not None:
            if not self.dragged:
                self.select_index(self.index_at(event.position()))
            self.drag_start = None
            self.dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.hover_index = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if steps and self.index_at(event.position()) is not None:
            self.zoom_history(steps)
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, event):
        if not self.history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, surface, text, grid, actual_color, forecast_color, fill = self.colors
        painter.fillRect(self.rect(), QColor(surface))
        history = self.visible_history()
        result = self.result
        show_forecast = self.forecast_visible()
        lower, upper = self.axis_limits or self.price_bounds()
        values = [lower + (upper - lower) * index / 4 for index in range(5)]
        labels = [f"{value:.2f}" if .01 <= abs(value) < 1_000_000 else f"{value:.3g}" for value in values]
        font = QFontMetricsF(painter.font())
        left_margin = max(font.horizontalAdvance(label) for label in labels) + 24
        top_margin = font.height() + 8
        bottom_margin = font.height() * 2 + 12
        chart = QRectF(left_margin, top_margin, self.width() - left_margin - 24,
                       self.height() - top_margin - bottom_margin)
        self.plot_area = chart
        last = len(history) + (len(result.forecast) if show_forecast else 0) - 1

        def point(index, price):
            return QPointF(chart.left() + chart.width() * index / max(last, 1),
                           chart.bottom() - chart.height() * (price - lower) / (upper - lower))

        for value, label in zip(values, labels):
            left, right = point(0, value), point(last, value)
            painter.setPen(QColor(grid))
            painter.drawLine(left, right)
            painter.setPen(QColor(text))
            label_x = chart.left() - font.horizontalAdvance(label) - 12
            painter.drawText(QPointF(label_x, left.y() + font.ascent() / 2), label)

        painter.save()
        painter.setClipRect(chart.adjusted(-1, -1, 1, 1))
        start = len(history) - 1
        llama_color = QColor("#fbbf65" if QColor(surface).lightness() < 128 else "#9a4900")
        if show_forecast:
            boundary = point(start, lower).x()
            painter.setPen(QPen(QColor(grid), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(boundary, chart.top()), QPointF(boundary, chart.bottom()))
        if show_forecast and self.layers["interval"]:
            top = [point(start, history[-1])] + [
                point(start + step, value) for step, value in enumerate(result.upper, 1)
            ]
            bottom = [point(start + step, value) for step, value in enumerate(result.lower, 1)]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawPolygon(QPolygonF(top + list(reversed(bottom))))
        if self.layers["close"]:
            painter.setPen(QPen(QColor(actual_color), 2))
            painter.drawPolyline(QPolygonF([point(i, value) for i, value in enumerate(history)]))
        if self.layers["average"]:
            average_color = QColor(actual_color)
            average_color = average_color.lighter(145) if QColor(surface).lightness() < 128 else average_color.darker(145)
            painter.setPen(QPen(average_color, 2, Qt.PenStyle.DotLine))
            painter.drawPolyline(QPolygonF([point(i, value) for i, value in self.moving_average()]))
        if show_forecast and self.layers["forecast"]:
            painter.setPen(QPen(QColor(forecast_color), 2, Qt.PenStyle.DashLine))
            painter.drawPolyline(QPolygonF([
                point(start + i, value) for i, value in enumerate([history[-1], *result.forecast])
            ]))
        if show_forecast and self.llama_band_visible():
            bounds = [self.llama_bounds(step) for step in range(len(self.llama_forecast))]
            border = QColor(llama_color)
            border.setAlpha(180)
            painter.setPen(QPen(border, 1.5, Qt.PenStyle.DotLine))
            for values in zip(*bounds):
                painter.drawPolyline(QPolygonF([
                    point(start + i, value) for i, value in enumerate([history[-1], *values])
                ]))
        if show_forecast and self.llama_visible():
            painter.setPen(QPen(llama_color, 2, Qt.PenStyle.DashDotLine))
            painter.drawPolyline(QPolygonF([
                point(start + i, value) for i, value in enumerate([history[-1], *self.llama_forecast])
            ]))
        for index, radius in ((self.hover_index, 2), (self.selected_index, 4)):
            if index is not None:
                x = point(index, lower).x()
                painter.setPen(QPen(QColor(text), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
                if index < len(history):
                    markers = [(history[index], QColor(actual_color))]
                else:
                    step = index - len(history)
                    markers = []
                    if self.layers["forecast"] or self.layers["interval"]:
                        markers.append((result.forecast[step], QColor(forecast_color)))
                    if self.llama_visible():
                        markers.append((self.llama_forecast[step], llama_color))
                for price, color in markers:
                    painter.setPen(color)
                    painter.setBrush(color)
                    painter.drawEllipse(point(index, price), radius, radius)
        painter.restore()
        painter.setPen(QColor(text))
        days = self.visible_days()
        baseline = chart.bottom() + font.height() + 6
        painter.drawText(QPointF(chart.left(), baseline), str(days[0]))
        end_label = str(days[-1])
        end_x = point(start, lower).x()
        if show_forecast:
            horizon_label = f"+{len(result.forecast)}"
            horizon_x = chart.right() - font.horizontalAdvance(horizon_label)
            painter.drawText(QPointF(horizon_x, baseline), horizon_label)
            end_x = min(horizon_x - font.horizontalAdvance(end_label) - 12, end_x)
        else:
            end_x = min(chart.right() - font.horizontalAdvance(end_label), end_x)
        if end_x > chart.left() + font.horizontalAdvance(str(days[0])) + 16:
            painter.drawText(QPointF(end_x, baseline), end_label)
        painter.end()
