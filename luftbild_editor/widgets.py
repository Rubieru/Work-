"""Bedienelemente: Schieberegler, Histogramm und Bildansicht mit Zuschneiderahmen."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QTransform)
from PySide6.QtWidgets import (QDoubleSpinBox, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QSlider, QVBoxLayout,
                               QWidget)

from . import theme

ACCENT = QColor(theme.ACCENT)


class LabeledSlider(QWidget):
    """Regler mit Beschriftung und Zahlenfeld. Doppelklick auf den Namen = Standardwert."""

    valueChanged = Signal(float)

    def __init__(self, title, minimum, maximum, step, default, decimals=2,
                 suffix="", display_factor=1.0, tooltip="", parent=None):
        super().__init__(parent)
        self._min, self._max, self._step = minimum, maximum, step
        self._default = default
        self._factor = display_factor

        self.label = QLabel(title)
        self.label.setToolTip((tooltip + "\n\n" if tooltip else "") +
                              "Doppelklick auf den Namen setzt den Regler zurück.")
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(minimum * display_factor, maximum * display_factor)
        self.spin.setSingleStep(step * display_factor)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(84)
        self.spin.setKeyboardTracking(False)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, round((maximum - minimum) / step))
        self.slider.setToolTip(tooltip)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.label)
        top.addStretch(1)
        top.addWidget(self.spin)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        lay.addLayout(top)
        lay.addWidget(self.slider)

        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.label.mouseDoubleClickEvent = lambda e: self.reset()
        self.setValue(default)

    def value(self) -> float:
        return round(self.spin.value() / self._factor, 6)

    def setValue(self, v: float) -> None:
        v = min(max(float(v), self._min), self._max)
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(round((v - self._min) / self._step))
        self.spin.setValue(v * self._factor)
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def reset(self):
        self.setValue(self._default)
        self.valueChanged.emit(self.value())

    def _from_slider(self, i):
        v = self._min + i * self._step
        self.spin.blockSignals(True)
        self.spin.setValue(v * self._factor)
        self.spin.blockSignals(False)
        self.valueChanged.emit(self.value())

    def _from_spin(self, dv):
        self.slider.blockSignals(True)
        self.slider.setValue(round((dv / self._factor - self._min) / self._step))
        self.slider.blockSignals(False)
        self.valueChanged.emit(self.value())


class HistogramWidget(QWidget):
    """Histogramm vorher und nachher, wie in Bildbearbeitungsprogrammen (linear).

    Unter jedem Histogramm eine Graustufen-Leiste; rote Markierung, wenn Pixel bei
    0 oder 255 anstoßen (Zeichnung verloren / NoData-Gefahr).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self._before = None
        self._after = None
        self._levels = (0.0, 1.0)
        self.setToolTip("Oben: Original, unten: bearbeitet (schwarzer Rand nicht mitgezählt).\n"
                        "Ziel: breiter, flacher Verlauf über den ganzen Bereich, ohne Spitze\n"
                        "und ohne Werte genau bei 0 (Schwarz) oder 255 (Weiß).")

    def set_data(self, before, after, levels):
        self._before, self._after, self._levels = before, after, levels
        self.update()

    def _panel(self, p, r, data, title, levels=None):
        p.fillRect(r, QColor(250, 250, 250))
        p.setPen(QColor(190, 200, 198))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        bar_h = 8
        plot = r.adjusted(1, 16, -1, -bar_h - 2)
        p.setPen(QColor(90, 100, 98))
        p.drawText(r.adjusted(4, 1, -4, 0), Qt.AlignLeft | Qt.AlignTop, title)
        if data is not None and data.sum() > 0:
            d = data.astype(np.float64)
            n = len(d)
            # Höhe: 99,5-%-Wert statt Maximum, damit einzelne Spitzen nicht alles stauchen
            top = max(np.percentile(d, 99.5), 1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(70, 80, 78))
            bw = plot.width() / n
            for i, v in enumerate(d):
                hgt = min(v / top, 1.0) * plot.height()
                if hgt > 0:
                    p.drawRect(QRectF(plot.left() + i * bw, plot.bottom() - hgt,
                                      max(bw, 1.0), hgt))
            total = d.sum()
            lo_pct = d[0] / total * 100
            hi_pct = d[-1] / total * 100
            p.setPen(QColor(200, 40, 40))
            for pct, x, al in ((lo_pct, plot.left() + 2, Qt.AlignLeft),
                               (hi_pct, plot.right() - 2, Qt.AlignRight)):
                if pct >= 0.05:
                    p.fillRect(QRectF(x - 2 if al == Qt.AlignRight else x - 2, plot.top(), 3,
                                      plot.height()), QColor(220, 40, 40))
                    p.drawText(QRectF(plot.left() + 4, plot.top(), plot.width() - 8, 14),
                               al | Qt.AlignTop, f"{pct:.1f} %")
        if levels and levels != (0.0, 1.0):
            p.setPen(QPen(ACCENT, 1, Qt.DashLine))
            for v in levels:
                x = plot.left() + plot.width() * min(max(v, 0), 1)
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        grad = QLinearGradient(r.left(), 0, r.right(), 0)
        grad.setColorAt(0, QColor(0, 0, 0))
        grad.setColorAt(1, QColor(255, 255, 255))
        p.fillRect(QRectF(r.left() + 1, r.bottom() - bar_h, r.width() - 1, bar_h), grad)

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        half = r.height() // 2
        self._panel(p, QRectF(r.left(), r.top(), r.width(), half - 3), self._before,
                    "Original", self._levels)
        self._panel(p, QRectF(r.left(), r.top() + half + 1, r.width(), half - 1), self._after,
                    "Bearbeitet")
        p.end()


class CurveWidget(QWidget):
    """Gradationskurve zum manuellen Nachbearbeiten des Histogramms.

    Klick auf die Kurve setzt einen Punkt, Ziehen verschiebt ihn, Doppelklick oder
    rechte Maustaste entfernt ihn. Im Hintergrund das aktuelle Histogramm.
    """

    curveChanged = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._points: list = []
        self._hist = None
        self._drag = None
        self.setToolTip("Klicken: Punkt setzen · Ziehen: verschieben · Doppelklick oder "
                        "rechte Maustaste: Punkt löschen.\nKurve nach oben = heller, "
                        "S-Form = mehr Kontrast in den Mitteltönen.")

    def set_points(self, points):
        self._points = [tuple(p) for p in points]
        self.update()

    def set_histogram(self, hist):
        self._hist = hist
        self.update()

    def _area(self):
        return QRectF(self.rect()).adjusted(8, 8, -8, -8)

    def _to_px(self, x, y):
        a = self._area()
        return QPointF(a.left() + x * a.width(), a.bottom() - y * a.height())

    def _from_px(self, pos):
        a = self._area()
        return (min(max((pos.x() - a.left()) / a.width(), 0.0), 1.0),
                min(max((a.bottom() - pos.y()) / a.height(), 0.0), 1.0))

    def _nearest(self, pos, tol=10):
        for i, (x, y) in enumerate(self._points):
            if (self._to_px(x, y) - pos).manhattanLength() <= tol:
                return i
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        i = self._nearest(pos)
        if e.button() == Qt.RightButton:
            if i is not None:
                self._points.pop(i)
                self._emit()
            return
        if i is None:
            x, y = self._from_px(pos)
            if 0.01 < x < 0.99:
                self._points.append((x, y))
                self._points.sort()
                i = self._points.index((x, y))
                self._emit()
        self._drag = i

    def mouseDoubleClickEvent(self, e):
        i = self._nearest(e.position())
        if i is not None:
            self._points.pop(i)
            self._drag = None
            self._emit()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        x, y = self._from_px(e.position())
        x = min(max(x, 0.01), 0.99)
        self._points[self._drag] = (x, y)
        order = sorted(range(len(self._points)), key=lambda k: self._points[k][0])
        self._points = [self._points[k] for k in order]
        self._drag = order.index(self._drag)
        self.update()
        self._emit()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def _emit(self):
        self.update()
        self.curveChanged.emit(tuple((round(x, 4), round(y, 4)) for x, y in self._points))

    def paintEvent(self, _):
        from .processing import curve_lut
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = self._area()
        p.fillRect(self.rect(), QColor(250, 250, 250))
        p.setPen(QColor(220, 226, 225))
        for i in range(1, 4):
            p.drawLine(self._to_px(i / 4, 0), self._to_px(i / 4, 1))
            p.drawLine(self._to_px(0, i / 4), self._to_px(1, i / 4))
        if self._hist is not None and self._hist.sum() > 0:
            d = self._hist / max(np.percentile(self._hist, 99.5), 1.0)
            path = QPainterPath(self._to_px(0, 0))
            for i, v in enumerate(d):
                path.lineTo(self._to_px(i / (len(d) - 1), min(v, 1.0) * 0.9))
            path.lineTo(self._to_px(1, 0))
            p.fillPath(path, QColor(150, 160, 158, 110))
        p.setPen(QPen(QColor(170, 175, 174), 1, Qt.DashLine))
        p.drawLine(self._to_px(0, 0), self._to_px(1, 1))
        lut = curve_lut(self._points, 256)
        ys = lut if lut is not None else np.linspace(0, 1, 256)
        path = QPainterPath(self._to_px(0, float(ys[0])))
        for i in range(1, 256):
            path.lineTo(self._to_px(i / 255, float(ys[i])))
        p.setPen(QPen(ACCENT, 2))
        p.drawPath(path)
        p.setBrush(QColor(255, 255, 255))
        for x, y in self._points:
            p.drawEllipse(self._to_px(x, y), 5, 5)
        p.setPen(QColor(190, 200, 198))
        p.setBrush(Qt.NoBrush)
        p.drawRect(a)
        p.end()


class ImageView(QGraphicsView):
    """Bildanzeige mit Zoom (Mausrad) und pixelgenauem Zuschneiderahmen.

    Szenenkoordinaten = Pixel des Originals (Zuschneidemodus) bzw. des
    zugeschnittenen Bildes (Bearbeitungsmodus).
    """

    viewChanged = Signal()
    cropChanged = Signal(tuple)
    pixelHovered = Signal(int, int)
    cornerStep = Signal(int)  # Zwei-Klick-Modus: 1 = erste Ecke gesetzt, 2 = fertig

    HANDLE = 9  # Bildschirmpixel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(theme.VIEW_BACKGROUND))
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setFrameShape(QGraphicsView.NoFrame)

        self.pix_item = QGraphicsPixmapItem()
        self.detail_item = QGraphicsPixmapItem()
        self.detail_item.setZValue(1)
        for it in (self.pix_item, self.detail_item):
            it.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
            self.scene().addItem(it)

        self.mode = "edit"
        self.crop_rect = QRectF()
        self.aspect: float | None = None
        self.show_grid = False
        self.size_locked = False
        self.corner_mode = False
        self._corner_a = None
        self._hover = None
        self.loupe = QLabel(self)
        self.loupe.setFixedSize(176, 176)
        self.loupe.setStyleSheet("background: #222;")
        self.loupe.hide()
        self._drag = None
        self._pan = None
        self._img_size = (0, 0)
        self._pix_scale = 1.0
        self._rotation = 0.0
        self._detail_params = None
        self._has_image = False

        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewChanged.emit())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewChanged.emit())
        self.set_mode("edit")

    # ---------------------------------------------------------------- Bild
    def set_image(self, qimage, size, scale=1.0, rotation=0.0):
        """qimage zeigt ein Bild der Größe size (w, h) verkleinert um scale."""
        first = not self._has_image or size != self._img_size
        self._img_size = size
        self._pix_scale = scale
        self.pix_item.setPixmap(QPixmap.fromImage(qimage))
        self.set_display_rotation(rotation)
        self.scene().setSceneRect(QRectF(0, 0, *size))
        self._has_image = True
        self._update_smoothing()
        if first:
            self.fit()

    def _rotation_transform(self, angle):
        w, h = self._img_size
        cx, cy = (w - 1) / 2, (h - 1) / 2
        if not angle:
            return QTransform()
        return QTransform.fromTranslate(-cx, -cy) * QTransform().rotate(angle) \
            * QTransform.fromTranslate(cx, cy)

    def set_display_rotation(self, angle):
        """Schnelle Vorschau der Drehung im Zuschneidemodus (um die Bildmitte)."""
        self._rotation = angle
        rot = self._rotation_transform(angle)
        self.pix_item.setTransform(
            QTransform.fromScale(1 / self._pix_scale, 1 / self._pix_scale) * rot)
        if self._detail_params:
            x, y, scale = self._detail_params
            self.detail_item.setTransform(
                QTransform.fromScale(1 / scale, 1 / scale) * QTransform.fromTranslate(x, y)
                * rot)
        self.viewport().update()

    def visible_source_rect(self) -> QRectF:
        """Sichtbarer Bereich in Pixeln des (ungedrehten) Quellbildes."""
        poly = self.mapToScene(self.viewport().rect())
        r = self.pix_item.mapFromScene(poly).boundingRect()
        s = self._pix_scale
        return QRectF(r.x() / s, r.y() / s, r.width() / s, r.height() / s)

    def set_detail(self, qimage, x, y, scale, rotation=0.0):
        """Ausschnitt in voller Auflösung über die verkleinerte Ansicht legen."""
        self.detail_item.setPixmap(QPixmap.fromImage(qimage))
        self._detail_params = (x, y, scale)
        self.detail_item.setTransform(
            QTransform.fromScale(1 / scale, 1 / scale) * QTransform.fromTranslate(x, y)
            * self._rotation_transform(rotation))
        self.detail_item.show()

    def clear_detail(self):
        self.detail_item.hide()
        self.detail_item.setPixmap(QPixmap())
        self._detail_params = None

    def clear(self):
        self.pix_item.setPixmap(QPixmap())
        self.clear_detail()
        self._has_image = False

    # ---------------------------------------------------------------- Zoom
    def zoom(self) -> float:
        return self.transform().m11()

    def fit(self):
        if self._has_image:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            self._update_smoothing()
            self.viewChanged.emit()

    def actual_size(self):
        self.resetTransform()
        self._update_smoothing()
        self.viewChanged.emit()

    def zoom_by(self, factor):
        z = self.zoom() * factor
        if 0.005 <= z <= 40:
            self.scale(factor, factor)
            self._update_smoothing()
            self.viewChanged.emit()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.25 ** steps)

    def visible_rect(self) -> QRectF:
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        return r.intersected(self.sceneRect())

    def _update_smoothing(self):
        mode = Qt.FastTransformation if self.zoom() >= 2.0 else Qt.SmoothTransformation
        self.pix_item.setTransformationMode(mode)
        self.detail_item.setTransformationMode(mode)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.viewChanged.emit()

    # ---------------------------------------------------------------- Modus
    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "edit"
                         else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.OpenHandCursor if mode == "edit" else Qt.CrossCursor)
        self.viewport().update()

    def set_crop(self, rect: tuple | None):
        w, h = self._img_size
        if rect is None:
            rect = (0, 0, w, h)
        self.crop_rect = QRectF(*rect)
        self.viewport().update()

    def crop_tuple(self) -> tuple:
        r = self.crop_rect
        return (int(round(r.x())), int(round(r.y())),
                int(round(r.width())), int(round(r.height())))

    # ---------------------------------------------------------------- Maus
    HIT = 16  # Fangbereich der Anfasser in Bildschirmpixeln

    def _hit(self, pos) -> str | None:
        if self.crop_rect.isEmpty():
            return None
        r = self.mapFromScene(self.crop_rect).boundingRect()
        if self.size_locked:
            return "move" if r.adjusted(-self.HIT, -self.HIT, self.HIT, self.HIT).contains(pos) \
                else None
        t = self.HIT
        x, y = pos.x(), pos.y()
        near_l, near_r = abs(x - r.left()) <= t, abs(x - r.right()) <= t
        near_t, near_b = abs(y - r.top()) <= t, abs(y - r.bottom()) <= t
        inside_x = r.left() - t <= x <= r.right() + t
        inside_y = r.top() - t <= y <= r.bottom() + t
        h = ""
        if near_t and inside_x:
            h += "t"
        elif near_b and inside_x:
            h += "b"
        if near_l and inside_y:
            h += "l"
        elif near_r and inside_y:
            h += "r"
        if h:
            return h
        if r.contains(pos):
            return "move"
        return None

    _CURSORS = {"t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor,
                "r": Qt.SizeHorCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor, "move": Qt.SizeAllCursor}

    def set_corner_mode(self, on: bool):
        """Zwei-Klick-Modus: 1. Klick = obere linke Ecke, 2. Klick = untere rechte Ecke."""
        self.corner_mode = on
        self._corner_a = None
        self.loupe.setVisible(on)
        self.viewport().setCursor(Qt.CrossCursor)
        self.viewport().update()

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() in (Qt.MiddleButton, Qt.RightButton) or (
                self.mode == "crop" and e.button() == Qt.LeftButton
                and e.modifiers() & Qt.ControlModifier):
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if self.mode == "crop" and e.button() == Qt.LeftButton and self._has_image:
            sp = self.mapToScene(pos)
            if self.corner_mode:
                p = self._snap(sp)
                if self._corner_a is None:
                    self._corner_a = p
                    self.cornerStep.emit(1)
                else:
                    a = self._corner_a
                    self.crop_rect = QRectF(a, p).normalized()
                    self._corner_a = None
                    self.set_corner_mode(False)
                    self.cropChanged.emit(self.crop_tuple())
                    self.cornerStep.emit(2)
                self.viewport().update()
                return
            hit = self._hit(pos)
            if hit is None:
                # Neben den Rahmen geklickt: nur mit Umschalttaste einen neuen Rahmen
                # aufziehen, sonst das Bild verschieben – so verrutscht nichts aus Versehen.
                if e.modifiers() & Qt.ShiftModifier or self.crop_rect.isEmpty() \
                        or self._is_full_crop():
                    p = self._snap(sp)
                    self._drag = ("new", p, QRectF(p, p))
                else:
                    self._pan = e.position()
                    self.viewport().setCursor(Qt.ClosedHandCursor)
                    return
            else:
                self._drag = (hit, sp, QRectF(self.crop_rect))
            self._update_loupe(pos)
            return
        super().mousePressEvent(e)

    def _is_full_crop(self) -> bool:
        w, h = self._img_size
        return self.crop_rect == QRectF(0, 0, w, h)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        sp = self.mapToScene(pos)
        self.pixelHovered.emit(int(np.floor(sp.x())), int(np.floor(sp.y())))
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self.mode == "crop":
            if self._drag:
                self._drag_to(sp)
                self._update_loupe(pos)
            elif self.corner_mode:
                self._hover = self._snap(sp)
                self._update_loupe(pos)
                self.viewport().update()
            else:
                hit = self._hit(pos)
                self.viewport().setCursor(self._CURSORS.get(hit, Qt.CrossCursor))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.viewport().setCursor(Qt.OpenHandCursor if self.mode == "edit" else Qt.CrossCursor)
            return
        if self.mode == "crop" and self._drag:
            self._drag = None
            if not self.corner_mode:
                self.loupe.hide()
            if self.crop_rect.width() < 1 or self.crop_rect.height() < 1:
                self.set_crop(None)
            self.cropChanged.emit(self.crop_tuple())
            return
        super().mouseReleaseEvent(e)

    def _update_loupe(self, pos):
        """Lupe: 4-fach vergrößerter Ausschnitt um den Mauszeiger (pixelgenaues Setzen)."""
        n = 44
        src = self.viewport().grab(QRect(pos.x() - n // 2, pos.y() - n // 2, n, n))
        size = self.loupe.width()
        pm = src.scaled(size, size, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        p = QPainter(pm)
        p.setPen(QPen(ACCENT, 1))
        c = size // 2
        p.drawLine(c, 0, c, size)
        p.drawLine(0, c, size, c)
        p.setPen(QPen(ACCENT, 2))
        p.drawRect(0, 0, size - 1, size - 1)
        p.end()
        self.loupe.setPixmap(pm)
        # Lupe in die Ecke, die vom Mauszeiger am weitesten weg ist
        vw, vh = self.viewport().width(), self.viewport().height()
        x = 10 if pos.x() > vw / 2 else vw - size - 10
        y = 10 if pos.y() > vh / 2 else vh - size - 10
        self.loupe.move(x, y)
        self.loupe.show()

    def _snap(self, p: QPointF) -> QPointF:
        w, h = self._img_size
        return QPointF(min(max(round(p.x()), 0), w), min(max(round(p.y()), 0), h))

    def _drag_to(self, sp):
        kind, start, orig = self._drag
        p = self._snap(sp)
        w, h = self._img_size
        if kind == "move":
            dx = round(sp.x() - start.x())
            dy = round(sp.y() - start.y())
            r = QRectF(orig).translated(dx, dy)
            r.moveLeft(min(max(r.left(), 0), w - r.width()))
            r.moveTop(min(max(r.top(), 0), h - r.height()))
        elif kind == "new":
            r = QRectF(start, p).normalized()
            if self.aspect:
                r = self._fit_aspect(r, start, p)
        else:
            # Gegenüberliegende Ecke/Kante bleibt fest
            left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()
            if "l" in kind:
                left = min(p.x(), right - 1)
            if "r" in kind:
                right = max(p.x(), left + 1)
            if "t" in kind:
                top = min(p.y(), bottom - 1)
            if "b" in kind:
                bottom = max(p.y(), top + 1)
            r = QRectF(QPointF(left, top), QPointF(right, bottom))
            if self.aspect:
                if kind in ("t", "b"):
                    nw = round(r.height() * self.aspect)
                    r.setLeft(orig.center().x() - nw / 2)
                    r.setWidth(nw)
                else:
                    anchor = QPointF(orig.right() if "l" in kind else orig.left(),
                                     orig.bottom() if "t" in kind else orig.top())
                    if kind in ("l", "r"):
                        anchor.setY(orig.top())
                    r = self._fit_aspect(r, anchor, p)
        r = QRectF(round(r.x()), round(r.y()), round(r.width()), round(r.height()))
        self.crop_rect = r.intersected(QRectF(0, 0, w, h))
        self.viewport().update()
        self.cropChanged.emit(self.crop_tuple())

    def _fit_aspect(self, r, anchor, p):
        a = self.aspect
        wdt = abs(p.x() - anchor.x())
        hgt = wdt / a
        sx = 1 if p.x() >= anchor.x() else -1
        sy = 1 if p.y() >= anchor.y() else -1
        return QRectF(anchor, QPointF(anchor.x() + sx * wdt, anchor.y() + sy * hgt)).normalized()

    # ---------------------------------------------------------------- Zeichnen
    def drawForeground(self, painter, rect):
        if self.mode != "crop" or not self._has_image:
            return
        w, h = self._img_size
        full = QRectF(0, 0, w, h)
        z = self.zoom()
        cr = self.crop_rect if not self.crop_rect.isEmpty() else full

        # Rotationshilfe: gleichmäßiges Raster über das ganze Bild
        if self.show_grid:
            painter.setPen(QPen(QColor(0, 220, 255, 140), 0))
            step = max(w, h) / 16
            x = step
            while x < w:
                painter.drawLine(QPointF(x, 0), QPointF(x, h))
                x += step
            y = step
            while y < h:
                painter.drawLine(QPointF(0, y), QPointF(w, y))
                y += step

        # Abdunkeln außerhalb des Zuschnitts (großzügig, deckt gedrehte Ecken ab)
        outer = QPainterPath()
        outer.addRect(full.adjusted(-w, -h, w, h))
        inner = QPainterPath()
        inner.addRect(cr)
        painter.fillPath(outer.subtracted(inner), QColor(0, 0, 0, 150))

        # Drittel-Linien
        painter.setPen(QPen(QColor(255, 255, 255, 70), 0))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            painter.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            painter.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))

        painter.setPen(QPen(ACCENT, 0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(cr)
        hs = self.HANDLE / z
        painter.setBrush(ACCENT)
        handles = () if self.size_locked or self.corner_mode else \
            (cr.left(), cr.center().x(), cr.right())
        # Zwei-Klick-Modus: gesetzte erste Ecke und Vorschau des Rahmens
        if self.corner_mode and self._corner_a is not None:
            a = self._corner_a
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(ACCENT, 0, Qt.DashLine))
            if self._hover is not None:
                painter.drawRect(QRectF(a, self._hover).normalized())
            painter.setPen(QPen(ACCENT, 0))
            painter.drawLine(QPointF(a.x() - 12 / z, a.y()), QPointF(a.x() + 12 / z, a.y()))
            painter.drawLine(QPointF(a.x(), a.y() - 12 / z), QPointF(a.x(), a.y() + 12 / z))
            painter.setBrush(ACCENT)
        for px in handles:
            for py in (cr.top(), cr.center().y(), cr.bottom()):
                if px == cr.center().x() and py == cr.center().y():
                    continue
                painter.drawRect(QRectF(px - hs / 2, py - hs / 2, hs, hs))

        # Pixelgitter bei starker Vergrößerung
        if z >= 12:
            vr = rect.intersected(full)
            painter.setPen(QPen(QColor(128, 128, 128, 90), 0))
            for x in range(int(vr.left()), int(vr.right()) + 2):
                painter.drawLine(QPointF(x, vr.top()), QPointF(x, vr.bottom()))
            for y in range(int(vr.top()), int(vr.bottom()) + 2):
                painter.drawLine(QPointF(vr.left(), y), QPointF(vr.right(), y))
