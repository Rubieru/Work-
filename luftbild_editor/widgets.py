"""Bedienelemente: Schieberegler, Histogramm und Bildansicht mit Zuschneiderahmen."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (QDoubleSpinBox, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QSlider, QVBoxLayout,
                               QWidget)

ACCENT = QColor(255, 196, 0)


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
    """Histogramm vorher (grau) und nachher (gelb) mit Schwarz-/Weißpunkt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self._before = None
        self._after = None
        self._levels = (0.0, 1.0)
        self.setToolTip("Grau: Helligkeitsverteilung des Originals\n"
                        "Gelb: nach der Bearbeitung\n"
                        "Striche: Schwarz- und Weißpunkt der Histogrammstreckung.\n"
                        "Ein gut gestrecktes Bild nutzt die ganze Breite.")

    def set_data(self, before, after, levels):
        self._before, self._after, self._levels = before, after, levels
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor(28, 28, 28))
        p.setPen(QColor(70, 70, 70))
        p.drawRect(r)
        for data, color in ((self._before, QColor(150, 150, 150, 110)),
                            (self._after, QColor(255, 196, 0, 150))):
            if data is None or data.max() <= 0:
                continue
            d = np.sqrt(data / data.max())  # Wurzel: kleine Werte sichtbar
            path = QPainterPath(QPointF(r.left(), r.bottom()))
            n = len(d)
            for i, v in enumerate(d):
                path.lineTo(r.left() + r.width() * i / (n - 1), r.bottom() - v * (r.height() - 4))
            path.lineTo(r.right(), r.bottom())
            path.closeSubpath()
            p.fillPath(path, color)
        lo, hi = self._levels
        if (lo, hi) != (0.0, 1.0):
            p.setPen(QPen(QColor(230, 230, 230), 1, Qt.DashLine))
            for v in (lo, hi):
                x = r.left() + r.width() * min(max(v, 0), 1)
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        p.end()


class ImageView(QGraphicsView):
    """Bildanzeige mit Zoom (Mausrad) und pixelgenauem Zuschneiderahmen.

    Szenenkoordinaten = Pixel des Originals (Zuschneidemodus) bzw. des
    zugeschnittenen Bildes (Bearbeitungsmodus).
    """

    viewChanged = Signal()
    cropChanged = Signal(tuple)
    pixelHovered = Signal(int, int)

    HANDLE = 9  # Bildschirmpixel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(45, 45, 45))
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
    def _hit(self, pos) -> str | None:
        if self.crop_rect.isEmpty():
            return None
        r = self.mapFromScene(self.crop_rect).boundingRect()
        t = self.HANDLE
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

    def mousePressEvent(self, e):
        if e.button() in (Qt.MiddleButton, Qt.RightButton) or (
                self.mode == "crop" and e.button() == Qt.LeftButton
                and e.modifiers() & Qt.ControlModifier):
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if self.mode == "crop" and e.button() == Qt.LeftButton and self._has_image:
            sp = self.mapToScene(e.position().toPoint())
            hit = self._hit(e.position().toPoint())
            if hit is None:
                p = self._snap(sp)
                self._drag = ("new", p, QRectF(p, p))
            else:
                self._drag = (hit, sp, QRectF(self.crop_rect))
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
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
            else:
                hit = self._hit(e.position().toPoint())
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
            if self.crop_rect.width() < 1 or self.crop_rect.height() < 1:
                self.set_crop(None)
            self.cropChanged.emit(self.crop_tuple())
            return
        super().mouseReleaseEvent(e)

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
            left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()
            if "l" in kind:
                left = p.x()
            if "r" in kind:
                right = p.x()
            if "t" in kind:
                top = p.y()
            if "b" in kind:
                bottom = p.y()
            r = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
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
        for px in (cr.left(), cr.center().x(), cr.right()):
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
