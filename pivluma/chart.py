"""Interactive price, moving average, and forecast chart."""

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from pivluma.appearance import colors_for
from pivluma.forecast import TRAINING_WINDOW
from pivluma.localization import translate


class PriceChart(QWidget):
    selected = Signal(object)
    viewport_changed = Signal()

    def __init__(self, motion_enabled=False):
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
        self.calibrated_bands = ()
        self.comparison_symbol = ""
        self.comparison_closes = {}
        self.language = "en"
        self.layers = {"close": True, "average": False, "llama": True, "llama_band": True}
        self.motion_enabled = motion_enabled
        self.history_progress = 1.
        self.forecast_progress = 1.
        self.history_reveal = QVariantAnimation(self, duration=640)
        self.forecast_reveal = QVariantAnimation(self, duration=850)
        for animation, attribute in ((self.history_reveal, "history_progress"),
                                     (self.forecast_reveal, "forecast_progress")):
            animation.setStartValue(0.)
            animation.setEndValue(1.)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(lambda value, name=attribute: self.advance_reveal(name, value))
        self.drag_start = None
        self.drag_offset = 0
        self.dragged = False
        self.hover_index = None
        self.setAccessibleName("Closing prices and forecast interval")

    def advance_reveal(self, attribute, value):
        setattr(self, attribute, value)
        self.update()

    def reveal(self, *, history=False):
        animation = self.history_reveal if history else self.forecast_reveal
        if self.motion_enabled and self.isVisible():
            animation.stop()
            animation.start()
        self.update()

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
        return self.history_offset == 0 and self.llama_visible()

    def llama_visible(self):
        return self.layers["llama"] and bool(self.llama_forecast)

    def llama_band_visible(self):
        return self.layers["llama_band"] and self.llama_visible()

    def llama_bounds(self, step):
        if step < len(self.calibrated_bands) and (band := self.calibrated_bands[step]) is not None:
            return band.lower, band.upper
        center = self.llama_forecast[step]
        baseline = self.result.forecast[step]
        return (max(0., center - (baseline - self.result.lower[step])),
                center + (self.result.upper[step] - baseline))

    def comparison_anchor(self):
        for day, price in zip(self.days, self.history):
            if day in self.comparison_closes:
                return day, price / self.comparison_closes[day]
        return None

    def comparison_points(self):
        anchor = self.comparison_anchor()
        if anchor is None:
            return []
        _, scale = anchor
        points = [(index, self.comparison_closes[day] * scale)
                  for index, day in enumerate(self.visible_days()) if day in self.comparison_closes]
        return points if len(points) >= 2 else []

    def moving_average(self):
        start, end = self.bounds()
        return [(index - start, sum(self.history[index + 1 - TRAINING_WINDOW:index + 1]) / TRAINING_WINDOW)
                for index in range(max(start, TRAINING_WINDOW - 1), end)]

    def price_bounds(self):
        history = self.visible_history()
        values = [*history, *(value for _, value in self.comparison_points())]
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
        self.history_reveal.stop()
        self.forecast_reveal.stop()
        self.history_progress = self.forecast_progress = 1.
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
            text = template.format(date=self.visible_days()[index], price=history[index])
            value = dict(self.comparison_points()).get(index)
            if value is not None:
                template = translate(self.language, "{symbol} rebased: {value:.8g}")
                text += " · " + template.format(symbol=self.comparison_symbol, value=value)
            return text
        step = index - len(history)
        template = translate(self.language, "Forecast step {step} · llama.cpp {price:.8g}")
        text = template.format(step=step + 1, price=self.llama_forecast[step])
        if self.llama_band_visible():
            lower, upper = self.llama_bounds(step)
            calibrated = step < len(self.calibrated_bands) and self.calibrated_bands[step] is not None
            band = translate(self.language, "Calibrated llama band" if calibrated else "llama.cpp scenario band")
            text += f" · {band} [{lower:.8g}, {upper:.8g}]"
            if calibrated:
                entry = self.calibrated_bands[step]
                samples = translate(self.language, "{count} calibration samples").format(count=entry.samples)
                text += f" · {samples}"
                if entry.checked:
                    coverage = translate(self.language, "Past band coverage {percent:.0f}% ({count} checked).").format(
                        percent=100 * entry.covered / entry.checked, count=entry.checked
                    )
                    text += f" · {coverage}"
        return text

    def index_at(self, position):
        if not self.plot_area.contains(position) or self.plot_area.width() <= 0:
            return None
        count = len(self.visible_history()) + (len(self.llama_forecast) if self.forecast_visible() else 0)
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
        _, surface, text, grid, actual_color, _, _ = self.colors
        painter.fillRect(self.rect(), QColor(surface))
        history = self.visible_history()
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
        last = len(history) + (len(self.llama_forecast) if show_forecast else 0) - 1

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
        painter.save()
        history_width = point(start, lower).x() - chart.left()
        painter.setClipRect(QRectF(chart.left() - 1, chart.top() - 1,
                                   history_width * self.history_progress + 2, chart.height() + 2),
                            Qt.ClipOperation.IntersectClip)
        if self.layers["close"]:
            painter.setPen(QPen(QColor(actual_color), 2))
            painter.drawPolyline(QPolygonF([point(i, value) for i, value in enumerate(history)]))
        if self.layers["average"]:
            average_color = QColor(actual_color)
            average_color = average_color.lighter(145) if QColor(surface).lightness() < 128 else average_color.darker(145)
            painter.setPen(QPen(average_color, 2, Qt.PenStyle.DotLine))
            painter.drawPolyline(QPolygonF([point(i, value) for i, value in self.moving_average()]))
        comparison = self.comparison_points()
        if comparison:
            color = QColor("#c5a4ff" if QColor(surface).lightness() < 128 else "#6538a0")
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            painter.drawPolyline(QPolygonF([point(index, value) for index, value in comparison]))
        painter.restore()
        painter.save()
        if show_forecast:
            boundary = point(start, lower).x()
            painter.setClipRect(QRectF(boundary - 1, chart.top() - 1,
                                       (chart.right() - boundary) * self.forecast_progress + 2, chart.height() + 2),
                                Qt.ClipOperation.IntersectClip)
        if show_forecast and self.llama_band_visible():
            bounds = [self.llama_bounds(step) for step in range(len(self.llama_forecast))]
            border = QColor(llama_color)
            border.setAlpha(180)
            painter.setPen(QPen(border, 1.5, Qt.PenStyle.DotLine))
            shade = QColor(llama_color)
            shade.setAlpha(round(25 * self.forecast_progress))
            lower_points, upper_points = [
                [point(start, history[-1]), *(point(start + i, value) for i, value in enumerate(values, 1))]
                for values in zip(*bounds)
            ]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawPolygon(QPolygonF(upper_points + list(reversed(lower_points))))
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
        painter.restore()
        for index, radius in ((self.hover_index, 2), (self.selected_index, 4)):
            if index is not None:
                x = point(index, lower).x()
                painter.setPen(QPen(QColor(text), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
                if index < len(history):
                    markers = [(history[index], QColor(actual_color))]
                else:
                    step = index - len(history)
                    markers = [(self.llama_forecast[step], llama_color)]
                for price, color in markers:
                    painter.setPen(color)
                    painter.setBrush(color)
                    painter.drawEllipse(point(index, price), radius, radius)
        painter.restore()
        painter.setPen(QColor(text))
        days = self.visible_days()
        baseline = chart.bottom() + font.height() + 6
        date_width = max(font.horizontalAdvance(str(days[0])), font.horizontalAdvance(str(days[-1])))
        history_right = point(start, lower).x()
        horizon_x = chart.right()
        if show_forecast:
            horizon_label = f"+{len(self.llama_forecast)}"
            horizon_x -= font.horizontalAdvance(horizon_label)
            painter.drawText(QPointF(horizon_x, baseline), horizon_label)
        right_limit = horizon_x - (12 if show_forecast else 0)
        label_gap = date_width + 14
        tick_count = max(1, min(len(days) - 1, int((history_right - chart.left()) / label_gap)))
        next_free = chart.left()
        for index in sorted({round(start * slot / tick_count) for slot in range(tick_count + 1)}):
            label = str(days[index])
            width = font.horizontalAdvance(label)
            tick_x = point(index, lower).x()
            label_x = min(right_limit - width, max(chart.left(), tick_x - width / 2))
            if index and label_x < next_free:
                continue
            painter.drawLine(QPointF(tick_x, chart.bottom()), QPointF(tick_x, chart.bottom() + 4))
            painter.drawText(QPointF(label_x, baseline), label)
            next_free = label_x + width + 8
        painter.end()
