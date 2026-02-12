"""
PDF Cutter - A PyQt6-based PDF viewer and page-range extractor.

Features
--------
* Open / drag-and-drop a PDF from disk
* Scroll through pages, zoom in/out, fit-width / fit-page
* Navigate via bookmarks (Table of Contents) sidebar
* Add custom bookmarks (saved per session)
* Full-text search with hit highlighting
* Select & copy text
* Extract a page range to a new PDF with a sensible default filename
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import pymupdf as fitz  # PyMuPDF

from PyQt6.QtCore import (
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ---------- constants --------------------------------------------------
DPI_RENDER = 150
ZOOM_STEP  = 0.1
ZOOM_MIN   = 0.2
ZOOM_MAX   = 5.0
PAGE_GAP   = 10
BG_COLOR   = QColor(200, 200, 200)


# ======================================================================
#  PageCanvas  –  custom-painted widget inside the QScrollArea
# ======================================================================
class PageCanvas(QWidget):
    """
    Paints pre-rendered page QPixmaps vertically with gaps.
    Reports its own sizeHint so the scroll area generates scrollbars.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.pixmaps: list[QPixmap] = []
        self._page_y: list[int] = []
        self._total_h: int = 0
        self._max_w: int = 0

        # rubber-band selection
        self._sel_origin: Optional[QPoint] = None
        self._sel_current: Optional[QPoint] = None

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ---- layout helpers ----------------------------------------------
    def _recompute(self):
        self._page_y.clear()
        y = PAGE_GAP
        max_w = 0
        for pm in self.pixmaps:
            self._page_y.append(y)
            y += pm.height() + PAGE_GAP
            max_w = max(max_w, pm.width())
        self._total_h = max(y, 1)
        self._max_w = max(max_w + PAGE_GAP * 2, 1)
        self.setFixedSize(self._max_w, self._total_h)

    def sizeHint(self) -> QSize:
        if self.pixmaps:
            return QSize(self._max_w, self._total_h)
        return QSize(100, 100)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ---- public API --------------------------------------------------
    def set_pixmaps(self, pixmaps: list[QPixmap]):
        self.pixmaps = pixmaps
        self._recompute()
        self.update()

    def page_rect(self, idx: int) -> QRect:
        if 0 <= idx < len(self.pixmaps):
            pm = self.pixmaps[idx]
            x = max(0, (self.width() - pm.width()) // 2)
            return QRect(x, self._page_y[idx], pm.width(), pm.height())
        return QRect()

    def page_at_y(self, y: int) -> int:
        for i in range(len(self._page_y) - 1, -1, -1):
            if y >= self._page_y[i]:
                return i
        return 0

    # ---- painting ----------------------------------------------------
    def paintEvent(self, _event):
        if not self.pixmaps:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        vis = self.visibleRegion().boundingRect()

        for i, pm in enumerate(self.pixmaps):
            y = self._page_y[i]
            if y + pm.height() < vis.top() or y > vis.bottom():
                continue
            x = max(0, (self.width() - pm.width()) // 2)
            # shadow
            painter.fillRect(x + 3, y + 3, pm.width(), pm.height(),
                             QColor(160, 160, 160))
            # border
            painter.setPen(QPen(QColor(180, 180, 180), 1))
            painter.drawRect(x - 1, y - 1, pm.width() + 1, pm.height() + 1)
            # page image
            painter.drawPixmap(x, y, pm)

        # rubber-band
        if self._sel_origin and self._sel_current:
            r = QRect(self._sel_origin, self._sel_current).normalized()
            painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 120, 215, 40))
            painter.drawRect(r)

        painter.end()

    # ---- mouse -------------------------------------------------------
    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._sel_origin = ev.pos()
            self._sel_current = ev.pos()
            self.update()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QMouseEvent):
        if self._sel_origin is not None:
            self._sel_current = ev.pos()
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton and self._sel_origin:
            self._sel_current = ev.pos()
            self.update()
        super().mouseReleaseEvent(ev)

    def clear_selection(self):
        self._sel_origin = None
        self._sel_current = None
        self.update()

    def selection_rect(self) -> Optional[QRect]:
        if self._sel_origin and self._sel_current:
            return QRect(self._sel_origin, self._sel_current).normalized()
        return None


# ======================================================================
#  PdfViewerWidget  (QScrollArea wrapping PageCanvas)
# ======================================================================
class PdfViewerWidget(QScrollArea):
    page_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.doc: Optional[fitz.Document] = None
        self.zoom: float = 1.0
        self.search_rects: dict[int, list[fitz.Rect]] = {}
        self._current_page: int = 0
        self._page_png_data: list[bytes] = []

        self.canvas = PageCanvas()
        self.canvas.setAutoFillBackground(True)
        pal = self.canvas.palette()
        pal.setColor(QPalette.ColorRole.Window, BG_COLOR)
        self.canvas.setPalette(pal)

        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ---- document management -----------------------------------------
    def load_document(self, doc: fitz.Document):
        self.doc = doc
        self.search_rects.clear()
        self._render_all()
        self.scroll_to_page(0)

    def close_document(self):
        self.doc = None
        self._page_png_data.clear()
        self.canvas.set_pixmaps([])

    # ---- rendering ---------------------------------------------------
    def _render_all(self):
        self._page_png_data.clear()
        if not self.doc:
            self.canvas.set_pixmaps([])
            return

        scale = DPI_RENDER / 72.0 * self.zoom
        mat = fitz.Matrix(scale, scale)
        pixmaps: list[QPixmap] = []

        for i in range(len(self.doc)):
            page = self.doc[i]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png = pix.tobytes("png")
            self._page_png_data.append(png)

            img = QImage()
            ok = img.loadFromData(png)

            # search highlights
            if i in self.search_rects:
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setOpacity(0.40)
                p.setBrush(QColor(255, 255, 0))
                p.setPen(Qt.PenStyle.NoPen)
                for r in self.search_rects[i]:
                    qr = QRectF(r.x0 * scale, r.y0 * scale,
                                r.width * scale, r.height * scale)
                    p.drawRect(qr)
                p.end()

            pixmaps.append(QPixmap.fromImage(img))

        self.canvas.set_pixmaps(pixmaps)

    # ---- zoom --------------------------------------------------------
    def set_zoom(self, zoom: float):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if abs(zoom - self.zoom) < 0.001:
            return
        sb = self.verticalScrollBar()
        rel = sb.value() / max(sb.maximum(), 1)
        self.zoom = zoom
        self._render_all()
        QTimer.singleShot(0, lambda: sb.setValue(int(rel * sb.maximum())))

    def zoom_in(self):
        self.set_zoom(self.zoom + ZOOM_STEP)

    def zoom_out(self):
        self.set_zoom(self.zoom - ZOOM_STEP)

    def fit_width(self):
        if not self.doc:
            return
        pw = self.doc[0].rect.width * DPI_RENDER / 72.0
        vw = self.viewport().width() - 40
        self.set_zoom(vw / pw)

    def fit_page(self):
        if not self.doc:
            return
        pw = self.doc[0].rect.width * DPI_RENDER / 72.0
        ph = self.doc[0].rect.height * DPI_RENDER / 72.0
        vw = self.viewport().width() - 40
        vh = self.viewport().height() - 40
        self.set_zoom(min(vw / pw, vh / ph))

    # ---- navigation --------------------------------------------------
    def scroll_to_page(self, page_index: int):
        if not (0 <= page_index < len(self.canvas.pixmaps)):
            return
        y = self.canvas._page_y[page_index]
        self.verticalScrollBar().setValue(max(0, y - 10))
        self._current_page = page_index
        self.page_changed.emit(page_index)

    @property
    def current_page(self) -> int:
        return self._current_page

    def _on_scroll(self):
        if not self.canvas.pixmaps:
            return
        mid_y = self.verticalScrollBar().value() + self.viewport().height() // 2
        pg = self.canvas.page_at_y(mid_y)
        if pg != self._current_page:
            self._current_page = pg
            self.page_changed.emit(pg)

    # ---- search ------------------------------------------------------
    def search(self, text: str) -> int:
        self.search_rects.clear()
        if not self.doc or not text:
            self._render_all()
            return 0
        total = 0
        for i in range(len(self.doc)):
            rects = self.doc[i].search_for(text)
            if rects:
                self.search_rects[i] = rects
                total += len(rects)
        self._render_all()
        if self.search_rects:
            self.scroll_to_page(min(self.search_rects))
        return total

    def clear_search(self):
        self.search_rects.clear()
        self._render_all()

    # ---- text copy from selection ------------------------------------
    def _try_copy_selection(self):
        if not self.doc:
            return
        sel = self.canvas.selection_rect()
        if sel is None or sel.width() < 4 or sel.height() < 4:
            return
        scale = DPI_RENDER / 72.0 * self.zoom
        for i in range(len(self.canvas.pixmaps)):
            pr = self.canvas.page_rect(i)
            inter = sel.intersected(pr)
            if inter.isEmpty():
                continue
            local = QRect(inter.x() - pr.x(), inter.y() - pr.y(),
                          inter.width(), inter.height())
            clip = fitz.Rect(local.x() / scale, local.y() / scale,
                             local.right() / scale, local.bottom() / scale)
            text = self.doc[i].get_text("text", clip=clip).strip()
            if text:
                QGuiApplication.clipboard().setText(text)
                return

    # ---- Ctrl+scroll zoom -------------------------------------------
    def wheelEvent(self, ev: QWheelEvent):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if ev.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            ev.accept()
        else:
            super().wheelEvent(ev)

    # ---- relay mouse-up to copy text ---------------------------------
    def mouseReleaseEvent(self, ev: QMouseEvent):
        super().mouseReleaseEvent(ev)
        if ev.button() == Qt.MouseButton.LeftButton:
            self._try_copy_selection()
            self.canvas.clear_selection()


# ======================================================================
#  Export Dialog
# ======================================================================
class ExportDialog(QDialog):
    def __init__(self, total_pages: int, source_path: str,
                 current_page: int = 0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Export Page Range")
        self.setMinimumWidth(450)
        self.total_pages = total_pages
        self.source_path = source_path

        layout = QFormLayout(self)

        rg = QGroupBox("Page Range (1-based)")
        rg_l = QHBoxLayout(rg)
        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, total_pages)
        self.spin_from.setValue(max(1, current_page + 1))
        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, total_pages)
        self.spin_to.setValue(total_pages)
        rg_l.addWidget(QLabel("From:"))
        rg_l.addWidget(self.spin_from)
        rg_l.addWidget(QLabel("To:"))
        rg_l.addWidget(self.spin_to)
        layout.addRow(rg)

        self.edit_path = QLineEdit(self._default_path())
        btn = QPushButton("Browse...")
        btn.clicked.connect(self._browse)
        h = QHBoxLayout()
        h.addWidget(self.edit_path, 1)
        h.addWidget(btn)
        layout.addRow("Save as:", h)

        self.spin_from.valueChanged.connect(self._update_path)
        self.spin_to.valueChanged.connect(self._update_path)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._validate)
        bb.rejected.connect(self.reject)
        layout.addRow(bb)

    def _default_path(self) -> str:
        p = Path(self.source_path)
        return str(p.parent / f"{p.stem}_pages_{self.spin_from.value()}-{self.spin_to.value()}.pdf")

    def _update_path(self):
        self.edit_path.setText(self._default_path())

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF", self.edit_path.text(), "PDF (*.pdf)")
        if path:
            self.edit_path.setText(path)

    def _validate(self):
        if self.spin_from.value() > self.spin_to.value():
            QMessageBox.warning(self, "Invalid", "'From' must be <= 'To'.")
            return
        self.accept()

    @property
    def page_range(self) -> tuple[int, int]:
        return self.spin_from.value() - 1, self.spin_to.value() - 1

    @property
    def output_path(self) -> str:
        return self.edit_path.text()


# ======================================================================
#  Main Window
# ======================================================================
class PdfCutterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Cutter")
        self.resize(1100, 800)
        self.setAcceptDrops(True)

        self.doc: Optional[fitz.Document] = None
        self.doc_path: str = ""
        self.custom_bookmarks: list[tuple[str, int]] = []

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_shortcuts()

    # ---- UI ----------------------------------------------------------
    def _build_ui(self):
        self.viewer = PdfViewerWidget()
        self.viewer.page_changed.connect(self._on_page_changed)

        self.bookmark_tree = QTreeWidget()
        self.bookmark_tree.setHeaderLabel("Bookmarks (TOC)")
        self.bookmark_tree.itemClicked.connect(self._on_bm_click)

        self.custom_bm_list = QListWidget()
        self.custom_bm_list.itemClicked.connect(self._on_custom_bm_click)

        sidebar = QWidget()
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.addWidget(QLabel("Table of Contents"))
        sl.addWidget(self.bookmark_tree, 2)
        sl.addWidget(QLabel("Custom Bookmarks"))
        sl.addWidget(self.custom_bm_list, 1)
        btn_bm = QPushButton("Add Bookmark Here")
        btn_bm.clicked.connect(self._add_bookmark)
        sl.addWidget(btn_bm)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(sidebar)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 860])
        self.setCentralWidget(splitter)

        self.status_label = QLabel("No file loaded")
        self.page_label = QLabel("")
        self.zoom_label = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.status_label, 1)
        sb.addPermanentWidget(self.page_label)
        sb.addPermanentWidget(self.zoom_label)

    def _build_menu(self):
        mb = self.menuBar()

        fm = mb.addMenu("&File")
        a = fm.addAction("&Open PDF...")
        a.setShortcut(QKeySequence.StandardKey.Open)
        a.triggered.connect(self._open_dialog)
        a = fm.addAction("&Export Page Range...")
        a.setShortcut(QKeySequence("Ctrl+E"))
        a.triggered.connect(self._export)
        fm.addSeparator()
        a = fm.addAction("&Quit")
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)

        vm = mb.addMenu("&View")
        a = vm.addAction("Zoom In")
        a.setShortcut(QKeySequence("Ctrl+="))
        a.triggered.connect(self.viewer.zoom_in)
        a = vm.addAction("Zoom Out")
        a.setShortcut(QKeySequence("Ctrl+-"))
        a.triggered.connect(self.viewer.zoom_out)
        a = vm.addAction("Fit Width")
        a.triggered.connect(self.viewer.fit_width)
        a = vm.addAction("Fit Page")
        a.triggered.connect(self.viewer.fit_page)

        sm = mb.addMenu("&Search")
        a = sm.addAction("&Find...")
        a.setShortcut(QKeySequence.StandardKey.Find)
        a.triggered.connect(self._focus_search)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        tb.addAction("Open", self._open_dialog)
        tb.addAction("Export", self._export)
        tb.addSeparator()
        tb.addAction("+", self.viewer.zoom_in)
        tb.addAction("-", self.viewer.zoom_out)
        tb.addAction("Fit W", self.viewer.fit_width)
        tb.addAction("Fit P", self.viewer.fit_page)
        tb.addSeparator()

        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        for p in (50, 75, 100, 125, 150, 200, 300):
            self.zoom_combo.addItem(f"{p}%", p / 100)
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.activated.connect(self._on_zoom_combo)
        self.zoom_combo.lineEdit().returnPressed.connect(self._on_zoom_typed)
        tb.addWidget(QLabel(" Zoom: "))
        tb.addWidget(self.zoom_combo)
        tb.addSeparator()

        self.page_spin = QSpinBox()
        self.page_spin.setPrefix("Page ")
        self.page_spin.setRange(1, 1)
        self.page_spin.valueChanged.connect(
            lambda v: self.viewer.scroll_to_page(v - 1))
        tb.addWidget(self.page_spin)
        self.total_lbl = QLabel(" / 0")
        tb.addWidget(self.total_lbl)
        tb.addSeparator()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.setMaximumWidth(200)
        self.search_edit.returnPressed.connect(self._do_search)
        tb.addWidget(self.search_edit)
        tb.addAction("Find", self._do_search)
        tb.addAction("Clear", self._clear_search)

    def _build_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self, self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, self.viewer.fit_width)

    # ---- file --------------------------------------------------------
    def _open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf);;All (*)")
        if p:
            self._load_pdf(p)

    def _load_pdf(self, path: str):
        try:
            doc = fitz.open(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if self.doc:
            self.viewer.close_document()
            self.doc.close()
        self.doc = doc
        self.doc_path = path
        self.custom_bookmarks.clear()
        self.custom_bm_list.clear()
        self.viewer.load_document(doc)
        self._populate_bookmarks()
        n = len(doc)
        self.page_spin.setRange(1, n)
        self.total_lbl.setText(f" / {n}")
        self.setWindowTitle(f"PDF Cutter - {Path(path).name}")
        self.status_label.setText(
            f"Loaded: {Path(path).name}  ({n} pages)")

    # ---- bookmarks ---------------------------------------------------
    def _populate_bookmarks(self):
        self.bookmark_tree.clear()
        if not self.doc:
            return
        toc = self.doc.get_toc()
        if not toc:
            it = QTreeWidgetItem(["(No bookmarks)"])
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.bookmark_tree.addTopLevelItem(it)
            return
        stack: list[QTreeWidgetItem] = []
        for lvl, title, pg in toc:
            it = QTreeWidgetItem([title])
            it.setData(0, Qt.ItemDataRole.UserRole, pg - 1)
            while len(stack) >= lvl:
                stack.pop()
            if stack:
                stack[-1].addChild(it)
            else:
                self.bookmark_tree.addTopLevelItem(it)
            stack.append(it)
        self.bookmark_tree.expandAll()

    def _on_bm_click(self, item: QTreeWidgetItem, _col: int):
        pg = item.data(0, Qt.ItemDataRole.UserRole)
        if pg is not None:
            self.viewer.scroll_to_page(pg)

    def _add_bookmark(self):
        if not self.doc:
            return
        pg = self.viewer.current_page
        name, ok = QInputDialog.getText(
            self, "Add Bookmark",
            f"Name for page {pg + 1}:", text=f"Page {pg + 1}")
        if ok and name:
            self.custom_bookmarks.append((name, pg))
            it = QListWidgetItem(f"{name}  (p.{pg + 1})")
            it.setData(Qt.ItemDataRole.UserRole, pg)
            self.custom_bm_list.addItem(it)

    def _on_custom_bm_click(self, item: QListWidgetItem):
        pg = item.data(Qt.ItemDataRole.UserRole)
        if pg is not None:
            self.viewer.scroll_to_page(pg)

    # ---- search ------------------------------------------------------
    def _focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _do_search(self):
        t = self.search_edit.text().strip()
        if not t or not self.doc:
            return
        n = self.viewer.search(t)
        self.status_label.setText(
            f'Found {n} match{"es" if n != 1 else ""} for "{t}"')

    def _clear_search(self):
        self.search_edit.clear()
        self.viewer.clear_search()
        self.status_label.setText("")

    # ---- export ------------------------------------------------------
    def _export(self):
        if not self.doc:
            QMessageBox.information(self, "No PDF", "Open a PDF first.")
            return
        dlg = ExportDialog(len(self.doc), self.doc_path,
                           self.viewer.current_page, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s, e = dlg.page_range
        out = dlg.output_path
        try:
            nd = fitz.open()
            nd.insert_pdf(self.doc, from_page=s, to_page=e)
            nd.save(out)
            nd.close()
            QMessageBox.information(
                self, "Done",
                f"Saved pages {s + 1}-{e + 1} to:\n{out}")
            self.status_label.setText(f"Exported -> {Path(out).name}")
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))

    # ---- page / zoom tracking ----------------------------------------
    def _on_page_changed(self, pg: int):
        if self.doc:
            self.page_label.setText(
                f"Page {pg + 1} / {len(self.doc)}")
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(pg + 1)
        self.page_spin.blockSignals(False)
        self._sync_zoom()

    def _sync_zoom(self):
        zt = f"{int(self.viewer.zoom * 100)}%"
        self.zoom_combo.setCurrentText(zt)
        self.zoom_label.setText(f"Zoom: {zt}")

    def _on_zoom_combo(self, idx: int):
        d = self.zoom_combo.itemData(idx)
        if d is not None:
            self.viewer.set_zoom(d)
            self._sync_zoom()

    def _on_zoom_typed(self):
        try:
            v = float(
                self.zoom_combo.currentText().replace("%", "")) / 100
            self.viewer.set_zoom(v)
            self._sync_zoom()
        except ValueError:
            pass

    # ---- drag & drop -------------------------------------------------
    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            for u in ev.mimeData().urls():
                if u.toLocalFile().lower().endswith(".pdf"):
                    ev.acceptProposedAction()
                    return
        ev.ignore()

    def dropEvent(self, ev: QDropEvent):
        for u in ev.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self._load_pdf(p)
                return


# ======================================================================
#  Entry point
# ======================================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Cutter")
    app.setStyle("Fusion")

    # Light / white palette
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(245, 245, 245))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.Base,            QColor(255, 255, 255))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(240, 240, 240))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(255, 255, 220))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Text,            QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.Button,          QColor(235, 235, 235))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(200, 0, 0))
    pal.setColor(QPalette.ColorRole.Link,            QColor(0, 100, 200))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(0, 120, 215))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    win = PdfCutterWindow()
    win.show()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        win._load_pdf(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
