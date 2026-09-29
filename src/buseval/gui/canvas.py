"""Node canvas: drag blocks from the palette, draw source edges, color by share."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
)

from ..engine.graph import normalize_source
from .document import (
    ddr_card_text, ddr_level_rgb, ddr_peer_port, ddr_port, edge_flows, fill_rgb,
    node_captions, node_pos, rank_levels, text_is_white, traffic_shares, type_color,
)
from .i18n import t

NODE_W = 210
NODE_H = 88
# Dropping one of these connects it to DDR. Picture-chain blocks stay on their own wires.
DDR_CLIENTS = {"eth", "usb", "can", "can_dbc", "spi", "flash", "gpu", "vdec"}
STRIPE_W = 8
CAPTION_X = 14
CAPTION_W = NODE_W - CAPTION_X - 12


class PortItem(QGraphicsEllipseItem):
    def __init__(self, node: "NodeItem", kind: str):
        super().__init__(-6, -6, 12, 12, node)
        self.node = node
        self.kind = kind  # "in" or "out"
        self.setBrush(QBrush(QColor("#4a4a4a")))
        self.setPen(QPen(Qt.NoPen))
        if kind == "out":
            self.setPos(NODE_W, NODE_H / 2)
        else:
            self.setPos(0, NODE_H / 2)

    def mousePressEvent(self, event):
        if self.kind == "out":
            self.scene().views()[0].begin_link(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.kind == "out":
            view = self.scene().views()[0]
            view.finish_link(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class NodeItem(QGraphicsRectItem):
    def __init__(self, name: str, type_name: str, kind: str, caption: str):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.node_name = name
        self.type_name = type_name
        self.kind = kind
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.stripe = QGraphicsRectItem(0, 0, STRIPE_W, NODE_H, self)
        self.stripe.setPen(QPen(Qt.PenStyle.NoPen))
        self.stripe.setBrush(QBrush(QColor(type_color(type_name))))
        self.title = QGraphicsSimpleTextItem(name, self)
        self.title.setPos(14, 8)
        self.title.setFont(QFont("", 15))
        self.caption = QGraphicsTextItem(self)
        self.caption.setPos(CAPTION_X, 32)
        self.caption.setFont(QFont("", 10))
        self.caption.setTextWidth(CAPTION_W)
        self.caption.document().setDocumentMargin(0)
        self.caption.setPlainText(caption)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.detail = QGraphicsSimpleTextItem("", self)
        self.detail.setPos(14, 58)
        self.detail.setFont(QFont("", 10))
        self.occupancy = None
        if kind == "ddr":
            self.occupancy = QGraphicsSimpleTextItem("", self)
            self.occupancy.setFont(QFont("", 22))
            self.occupancy.setBrush(QBrush(QColor("#1a1a1a")))
            self.occupancy.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.ring = QGraphicsRectItem(-4, -4, NODE_W + 8, NODE_H + 8, self)
        self.ring.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.ring.setPen(QPen(QColor("#e67e22"), 2.5))
        self.ring.setVisible(False)
        self.ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.out_port = None
        self.in_port = None
        if kind == "pipeline":
            self.out_port = PortItem(self, "out")
            self.in_port = PortItem(self, "in")
        elif kind == "master" and type_name not in DDR_CLIENTS:
            self.out_port = PortItem(self, "out")
        self.set_idle()

    def _paint_text(self, color: QColor):
        brush = QBrush(color)
        self.title.setBrush(brush)
        self.detail.setBrush(brush)
        self.caption.setDefaultTextColor(color)

    def _relayout(self):
        cap_h = self.caption.boundingRect().height()
        detail_y = 34 + cap_h
        self.detail.setPos(CAPTION_X, detail_y)
        bottom = detail_y
        if self.detail.text():
            bottom += self.detail.boundingRect().height()
        height = max(NODE_H, bottom + 12)
        self.prepareGeometryChange()
        self.setRect(0, 0, NODE_W, height)
        self.stripe.setRect(0, 0, STRIPE_W, height)
        self.ring.setRect(-4, -4, NODE_W + 8, height + 8)
        if self.out_port is not None:
            self.out_port.setPos(NODE_W, height / 2)
        if self.in_port is not None:
            self.in_port.setPos(0, height / 2)
        self._place_occupancy()

    def _place_occupancy(self):
        if self.occupancy is None or not self.occupancy.text():
            return
        rect = self.rect()
        bounds = self.occupancy.boundingRect()
        left = rect.width() * 0.46
        right = rect.width() - 8
        x = left + max(0.0, (right - left - bounds.width()) / 2)
        y = (rect.height() - bounds.height()) / 2
        self.occupancy.setPos(x, max(4.0, y))

    def set_idle(self):
        self.setBrush(QBrush(QColor("#fbfcfe")))
        self.setPen(QPen(QColor("#5d7590")))
        self.detail.setText("")
        if self.occupancy is not None:
            self.occupancy.setText("")
        self._paint_text(QColor("#1a1a1a"))
        self._relayout()

    def set_error(self):
        pen = QPen(QColor("#c0392b"))
        pen.setWidth(2)
        self.setPen(pen)

    def set_focus_ring(self, on: bool):
        self.ring.setVisible(on)
        self.setZValue(2 if on else 0)

    def pulse(self, phase: int):
        if not self.ring.isVisible():
            return
        t = (math.sin(phase / 16 * math.pi) + 1) / 2
        color = QColor("#e67e22")
        color.setAlpha(160 + int(90 * t))
        self.ring.setPen(QPen(color, 2.2 + t * 1.6))

    def set_share(self, percent: float, mbps: float, t: float, measured: str = ""):
        rgb = fill_rgb(t)
        self.setBrush(QBrush(QColor(*rgb)))
        self.setPen(QPen(QColor("#333333")))
        color = QColor("white") if text_is_white(t) else QColor("#1a1a1a")
        self._paint_text(color)
        self.title.setText(f"{self.node_name}  {percent:.1f}%")
        line = f"{mbps:.0f} MB/s"
        if measured:
            line = measured
        self.detail.setText(line)
        self._relayout()

    def set_budget(self, rgb: tuple[int, int, int], occupancy: float | None = None):
        self.setBrush(QBrush(QColor(*rgb)))
        self.setPen(QPen(QColor("#333333")))
        self._paint_text(QColor("#1a1a1a"))
        if self.occupancy is not None:
            text = "" if occupancy is None else f"{occupancy * 100:.1f}%"
            self.occupancy.setText(text)
            self.occupancy.setBrush(QBrush(QColor("#1a1a1a")))
            self._place_occupancy()

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu
        if self.kind == "ddr":
            event.accept()
            return
        menu = QMenu()
        delete = menu.addAction(t("delete"))
        chosen = menu.exec(event.screenPos())
        if chosen == delete:
            self.scene().views()[0].window.delete_node(self.node_name)
        event.accept()

    def hoverEnterEvent(self, event):
        view = self.scene().views()[0]
        view.window.hover_node(self)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        view = self.scene().views()[0]
        view.window.hover_node(None)
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.scene().views()[0].window.edit_node(self.node_name)
        event.accept()

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsRectItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None and scene.views() and not getattr(scene.views()[0], "_rebuilding", False):
                scene.views()[0].on_node_moved(self)
        return result


def _ddr_origin(topology, index: int) -> tuple[float, float]:
    xs = []
    ys = []
    for node in list(topology.masters) + list(topology.pipelines):
        if not node.enabled:
            continue
        pos = node_pos(node)
        if pos is not None:
            xs.append(pos[0])
            ys.append(pos[1])
    if not xs:
        return 40.0, 40.0 + index * 110
    return max(xs) + 280.0, min(ys) + index * 110


_OUT = {
    "top": QPointF(0, -1),
    "right": QPointF(1, 0),
    "bottom": QPointF(0, 1),
    "left": QPointF(-1, 0),
}


def port_toward(node: "NodeItem", scene_point: QPointF) -> tuple[str, float]:
    """Side and position facing another point. u runs 0..1 along that side."""
    local = node.mapFromScene(scene_point)
    rect = node.rect()
    center = rect.center()
    half_w = rect.width() / 2 or 1
    half_h = rect.height() / 2 or 1
    dx = local.x() - center.x()
    dy = local.y() - center.y()
    if abs(dx) / half_w >= abs(dy) / half_h:
        side = "right" if dx >= 0 else "left"
        u = (local.y() - rect.top()) / (rect.height() or 1)
    else:
        side = "bottom" if dy >= 0 else "top"
        u = (local.x() - rect.left()) / (rect.width() or 1)
    return side, max(0.08, min(0.92, u))


def side_for_point(rect, local: QPointF) -> tuple[str, float]:
    """Nearest side to a point, for dragging a dot around the card."""
    x = min(max(local.x(), rect.left()), rect.right())
    y = min(max(local.y(), rect.top()), rect.bottom())
    choices = (
        (abs(x - rect.left()), "left", (y - rect.top()) / (rect.height() or 1)),
        (abs(rect.right() - x), "right", (y - rect.top()) / (rect.height() or 1)),
        (abs(y - rect.top()), "top", (x - rect.left()) / (rect.width() or 1)),
        (abs(rect.bottom() - y), "bottom", (x - rect.left()) / (rect.width() or 1)),
    )
    _dist, side, u = min(choices)
    return side, max(0.08, min(0.92, u))


def point_on_side(rect, side: str, u: float) -> QPointF:
    u = max(0.08, min(0.92, u))
    if side == "top":
        return QPointF(rect.left() + u * rect.width(), rect.top())
    if side == "right":
        return QPointF(rect.right(), rect.top() + u * rect.height())
    if side == "bottom":
        return QPointF(rect.left() + u * rect.width(), rect.bottom())
    return QPointF(rect.left(), rect.top() + u * rect.height())


def _clear_of_picture_port(node: "NodeItem", port: tuple[str, float]) -> tuple[str, float]:
    side, u = port
    if side == "right" and node.out_port is not None and abs(u - 0.5) < 0.12:
        return side, 0.78
    if side == "left" and node.in_port is not None and abs(u - 0.5) < 0.12:
        return side, 0.22
    return port


def _spread_ports(edges: list) -> None:
    if len(edges) < 2:
        return
    edges.sort(key=lambda edge: edge.dst_port[1])
    gap = min(b.dst_port[1] - a.dst_port[1] for a, b in zip(edges, edges[1:]))
    if gap >= 0.1:
        return
    last = len(edges) - 1
    for index, edge in enumerate(edges):
        edge.dst_port = (edge.dst_port[0], 0.12 + 0.76 * index / last)


class EdgeDot(QGraphicsEllipseItem):
    """Draggable end of a DDR wire. It stays on the card's border."""

    def __init__(self, node: NodeItem, edge: "EdgeItem", end: str):
        super().__init__(-7, -7, 14, 14, node)
        self.node = node
        self.edge = edge
        self.end = end
        self.setZValue(6)
        self.setPen(QPen(QColor("#ffffff"), 2))
        self.setBrush(QBrush(edge.module_color))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        local = self.node.mapFromScene(event.scenePos())
        side, u = side_for_point(self.node.rect(), local)
        if self.end == "src":
            self.edge.src_port = (side, u)
            self.edge.src_locked = True
        else:
            self.edge.dst_port = (side, u)
            self.edge.dst_locked = True
        self.edge.refresh()
        event.accept()

    def mouseReleaseEvent(self, event):
        port = self.edge.src_port if self.end == "src" else self.edge.dst_port
        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        if port is not None and view is not None:
            if self.end == "src":
                view.window.remember_ddr_port(self.node.node_name, None, port[0], port[1])
            else:
                view.window.remember_ddr_port(
                    self.edge.dst.node_name, self.edge.src.node_name, port[0], port[1]
                )
        event.accept()


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src: NodeItem, dst: NodeItem, memory: bool = False, anchor: float = 0.5):
        super().__init__()
        self.src = src
        self.dst = dst
        self.memory = memory
        self.anchor = anchor
        self.src_port: tuple[str, float] | None = None
        self.dst_port: tuple[str, float] | None = None
        self.src_locked = False
        self.dst_locked = False
        self.src_dot: EdgeDot | None = None
        self.dst_dot: EdgeDot | None = None
        self.module_color = QColor(type_color(src.type_name))
        self.mbps: float | None = None
        self.pct = 0.0
        self.hot = False
        self.dim = False
        self._width = 1.8
        self._share_t = 0.0
        self._phase = 0
        self.setPen(QPen(QColor("#5d6d7e"), self._width))
        self.setZValue(-1)
        self.arrow = QGraphicsPolygonItem(self)
        self.arrow.setBrush(QBrush(QColor("#5d6d7e")))
        self.arrow.setPen(QPen(Qt.PenStyle.NoPen))
        self.tag = QGraphicsSimpleTextItem(self)
        self.tag.setFont(QFont("", 8))
        self.tag.setBrush(QBrush(QColor("#1a1a1a")))
        self.tag.setVisible(False)
        if memory:
            self.arrow.setVisible(False)
        self.refresh()

    def refresh(self):
        if self.memory:
            self._refresh_memory()
            return
        from PySide6.QtGui import QPainterPath
        start = self.src.mapToScene(self.src.out_port.pos())
        end = self.dst.mapToScene(self.dst.in_port.pos())
        mid_x = (start.x() + end.x()) / 2
        ang = math.atan2(0.0, end.x() - mid_x) if end.x() != mid_x else math.pi
        ux, uy = math.cos(ang), math.sin(ang)
        tip = end
        line_end = QPointF(end.x() - 9 * ux, end.y() - 9 * uy)
        path = QPainterPath(start)
        path.cubicTo(QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), line_end)
        self.setPath(path)
        wing = 4.5
        px, py = -uy, ux
        self.arrow.setPolygon(QPolygonF([
            tip,
            QPointF(tip.x() - 11 * ux + wing * px, tip.y() - 11 * uy + wing * py),
            QPointF(tip.x() - 11 * ux - wing * px, tip.y() - 11 * uy - wing * py),
        ]))
        point = path.pointAtPercent(0.42)
        self.tag.setPos(point.x() + 4, point.y() - 18)
        self._paint()

    def _refresh_memory(self):
        """Dashed DMA wire in the module color, with a draggable dot on each end."""
        from PySide6.QtGui import QPainterPath
        if self.src_dot is None or self.dst_dot is None or self.src_port is None or self.dst_port is None:
            return
        self.src_dot.setPos(point_on_side(self.src.rect(), *self.src_port))
        self.dst_dot.setPos(point_on_side(self.dst.rect(), *self.dst_port))
        start = self.src.mapToScene(self.src_dot.pos())
        end = self.dst.mapToScene(self.dst_dot.pos())
        src_out = _OUT[self.src_port[0]]
        dst_out = _OUT[self.dst_port[0]]
        dist = math.hypot(end.x() - start.x(), end.y() - start.y())
        stub = max(16.0, min(36.0, dist * 0.2))
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + src_out.x() * stub, start.y() + src_out.y() * stub),
            QPointF(end.x() + dst_out.x() * stub, end.y() + dst_out.y() * stub),
            end,
        )
        self.setPath(path)
        point = path.pointAtPercent(0.5)
        self.tag.setPos(point.x() + 4, point.y() - 16)
        self._paint()

    def set_memory_label(self, read: float | None, write: float | None):
        if read is None or write is None:
            self.mbps = None
            self.tag.setVisible(False)
        else:
            self.mbps = read + write
            self.tag.setText(f"{t('read')} {read:.0f} / {t('write')} {write:.0f}")
            self.tag.setVisible(not self.dim)
        self._width = 1.5
        self.refresh()

    def set_flow(self, mbps: float | None, thickness: float, pct: float):
        self.mbps = mbps
        self.pct = pct
        self._share_t = max(0.0, min(1.0, thickness))
        self._width = 1.6
        if not self.hot:
            self.setZValue(-1)
        if mbps is None:
            self.tag.setVisible(False)
        else:
            self.tag.setText(f"{mbps:.0f} MB/s  {pct:.1f}%")
            self.tag.setVisible(not self.dim)
        self.refresh()

    def set_hot(self, hot: bool, dim: bool):
        self.hot = hot
        self.dim = dim
        self.setOpacity(0.22 if dim else 1.0)
        self.setZValue(1 if hot else -1)
        if self.mbps is not None:
            self.tag.setVisible(not dim)
        self._paint()

    def advance(self, phase: int):
        if not self.hot:
            return
        self._phase = phase
        self._paint()

    def _paint(self):
        if self.memory:
            color = QColor("#9aa6b2") if self.dim else self.module_color
            pen = QPen(color, 2.2 if self.hot else 1.6, Qt.PenStyle.DashLine)
            pen.setDashPattern([7, 4])
            if self.hot:
                pen.setDashOffset(-self._phase * 0.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            self.setPen(pen)
            brush = QBrush(color)
            if self.src_dot is not None:
                self.src_dot.setBrush(brush)
            if self.dst_dot is not None:
                self.dst_dot.setBrush(brush)
            return
        if self.hot:
            color = QColor("#d35400")
            pen = QPen(color, self._width + 0.8)
            pen.setDashPattern([3, 2.2])
            pen.setDashOffset(-self._phase * 0.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        else:
            color = QColor("#b0b8c0") if self.dim else self.module_color
            pen = QPen(color, self._width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.arrow.setBrush(QBrush(color))


class GraphView(QGraphicsView):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setScene(QGraphicsScene(self))
        self.scene().setBackgroundBrush(QBrush(QColor("#c9d7e6")))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QGraphicsView { background: #c9d7e6; border: none; }")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setAcceptDrops(True)
        self.nodes: dict[str, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self._link_src: NodeItem | None = None
        self._temp = None
        self._rebuilding = False
        self._panning = False
        self._pan_start = None
        self._blank_press = None
        self.flows: dict[tuple[str, str], float] = {}
        self.node_stats: dict[str, tuple[float, float, float]] = {}
        self._hot: str | None = None
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(45)
        self.scene().selectionChanged.connect(self._on_selection)

    def drawBackground(self, painter: QPainter, rect):
        painter.fillRect(rect, QColor("#c9d7e6"))

    def sync_scene_rect(self):
        """Match the scene to the window when the drawing fits, so empty canvases have no scrollbars."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        if not self.nodes:
            self.setSceneRect(visible)
            return
        content = self.scene().itemsBoundingRect().adjusted(-48, -48, 48, 48)
        if content.width() <= visible.width() and content.height() <= visible.height():
            self.setSceneRect(visible)
        else:
            self.setSceneRect(content)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._rebuilding:
            self.sync_scene_rect()

    def rebuild(self, topology):
        self._rebuilding = True
        try:
            self._rebuild(topology)
        finally:
            self._rebuilding = False

    def _rebuild(self, topology):
        self.scene().clear()
        self.nodes.clear()
        self.edges.clear()
        self._temp = None
        self._link_src = None
        self._hot = None
        captions = node_captions(topology)
        for node in list(topology.masters) + list(topology.pipelines):
            if not node.enabled:
                continue
            kind = "pipeline" if node in topology.pipelines else "master"
            item = NodeItem(node.name, node.type, kind, captions.get(node.name, node.type))
            pos = node_pos(node)
            item.setPos(QPointF(*(pos or (40, 40))))
            self.scene().addItem(item)
            self.nodes[node.name] = item
        show_ddr = not getattr(self.window, "at_home", False)
        if show_ddr:
            for index, channel in enumerate(topology.ddr_channels):
                item = NodeItem(channel.name, "ddr", "ddr", ddr_card_text(channel))
                pos = node_pos(channel)
                if pos is None:
                    pos = _ddr_origin(topology, index)
                item.setPos(QPointF(*pos))
                self.scene().addItem(item)
                self.nodes[channel.name] = item
        for node in list(topology.pipelines) + [m for m in topology.masters if m.type == "eth"]:
            dst = self.nodes.get(node.name)
            if dst is None:
                continue
            for src_name in normalize_source(node.source):
                src = self.nodes.get(src_name)
                if src is None:
                    continue
                edge = EdgeItem(src, dst)
                self.scene().addItem(edge)
                self.edges.append(edge)
        clients = [
            node for node in list(topology.masters) + list(topology.pipelines)
            if node.enabled and node.type in DDR_CLIENTS and node.name in self.nodes
        ]
        models = {node.name: node for node in list(topology.masters) + list(topology.pipelines)}
        for channel in topology.ddr_channels:
            ddr = self.nodes.get(channel.name)
            if ddr is None or not clients:
                continue
            for index, client in enumerate(clients):
                src = self.nodes[client.name]
                edge = EdgeItem(src, ddr, memory=True, anchor=(index + 1) / (len(clients) + 1))
                edge.src_port = ddr_port(models[client.name])
                edge.dst_port = ddr_peer_port(channel, client.name)
                edge.src_locked = edge.src_port is not None
                edge.dst_locked = edge.dst_port is not None
                self.scene().addItem(edge)
                edge.src_dot = EdgeDot(src, edge, "src")
                edge.dst_dot = EdgeDot(ddr, edge, "dst")
                self.edges.append(edge)
        self._settle_memory_ports()
        self.sync_scene_rect()
        if hasattr(self.window, "hover_node"):
            self.window.hover_node(None)

    def apply_prediction(self, prediction, measured: dict[str, str] | None = None):
        shares = traffic_shares(prediction)
        levels = rank_levels(shares)
        measured = measured or {}
        for item in prediction.items:
            node = self.nodes.get(item.name)
            if node is None:
                continue
            share = shares.get(item.name, 0.0)
            mbps = item.read_bw_mbps + item.write_bw_mbps
            node.set_share(share * 100, mbps, levels.get(item.name, 0.0), measured.get(item.name, ""))
            self.node_stats[item.name] = (share, item.read_bw_mbps, item.write_bw_mbps)
        self._paint_ddr_level(prediction)
        self.flows = edge_flows(self.window.topology, prediction)
        total = prediction.total_read_mbps + prediction.total_write_mbps
        for edge in self.edges:
            if edge.memory:
                stats = self.node_stats.get(edge.src.node_name)
                if stats is None:
                    edge.set_memory_label(None, None)
                else:
                    _share, read, write = stats
                    edge.set_memory_label(read, write)
                continue
            mbps = self.flows.get((edge.src.node_name, edge.dst.node_name))
            if mbps is None:
                edge.set_flow(None, 0.0, 0.0)
                continue
            pct = (mbps / total * 100) if total else 0.0
            edge.set_flow(mbps, levels.get(edge.src.node_name, 0.0), pct)
        for edge in self.edges:
            if edge.memory:
                edge.refresh()
        self.focus(self._hot)

    def _paint_ddr_level(self, prediction):
        thresholds = getattr(self.window, "topology", None)
        yellow = 0.6
        red = 0.8
        if thresholds is not None:
            yellow = float(thresholds.alert_thresholds.get("yellow", 0.6))
            red = float(thresholds.alert_thresholds.get("red", 0.8))
        for margin in prediction.margins:
            node = self.nodes.get(margin.name)
            if node is None or node.kind != "ddr" or margin.available_mbps <= 0:
                continue
            demand = margin.read_demand_mbps + margin.write_demand_mbps
            occupancy = demand / margin.available_mbps
            node.set_budget(ddr_level_rgb(occupancy, yellow, red), occupancy)

    def mark_errors(self, names: set[str]):
        for name in names:
            node = self.nodes.get(name)
            if node is not None:
                node.set_error()

    def clear_colors(self):
        self.flows = {}
        self.node_stats = {}
        for node in self.nodes.values():
            node.set_idle()
            node.title.setText(node.node_name)
        for edge in self.edges:
            if edge.memory:
                edge.set_memory_label(None, None)
            else:
                edge.set_flow(None, 0.0, 0.0)

    def _on_selection(self):
        if self._rebuilding:
            return
        chosen = [item for item in self.scene().selectedItems() if isinstance(item, NodeItem)]
        self.focus(chosen[0].node_name if chosen else None)

    def focus(self, name: str | None):
        self._hot = name if name in self.nodes else None
        neighbors = set()
        hot_edges = []
        if self._hot:
            neighbors.add(self._hot)
            for edge in self.edges:
                if edge.src.node_name == self._hot or edge.dst.node_name == self._hot:
                    hot_edges.append(edge)
                    neighbors.add(edge.src.node_name)
                    neighbors.add(edge.dst.node_name)
        for node_name, node in self.nodes.items():
            node.setOpacity(1.0 if not self._hot or node_name in neighbors else 0.28)
            node.set_focus_ring(node_name == self._hot)
        hot_ids = {id(edge) for edge in hot_edges}
        for edge in self.edges:
            edge.set_hot(id(edge) in hot_ids, bool(self._hot) and id(edge) not in hot_ids)

    def _tick(self):
        if not self._hot:
            return
        self._phase = (self._phase + 1) % 48
        node = self.nodes.get(self._hot)
        if node is not None:
            node.pulse(self._phase)
        for edge in self.edges:
            edge.advance(self._phase)

    def on_node_moved(self, item: NodeItem):
        pos = item.pos()
        self.window.remember_pos(item.node_name, pos.x(), pos.y())
        self._settle_memory_ports()
        for edge in self.edges:
            if not edge.memory and (edge.src is item or edge.dst is item):
                edge.refresh()

    def _settle_memory_ports(self):
        groups: dict[tuple, list] = {}
        for edge in self.edges:
            if not edge.memory:
                continue
            if not edge.src_locked:
                edge.src_port = _clear_of_picture_port(
                    edge.src, port_toward(edge.src, edge.dst.sceneBoundingRect().center())
                )
            if not edge.dst_locked:
                edge.dst_port = port_toward(edge.dst, edge.src.sceneBoundingRect().center())
                groups.setdefault((id(edge.dst), edge.dst_port[0]), []).append(edge)
        for group in groups.values():
            _spread_ports(group)
        for edge in self.edges:
            if edge.memory:
                edge.refresh()

    def begin_link(self, port: PortItem):
        self._link_src = port.node

    def finish_link(self, scene_pos: QPointF):
        src = self._link_src
        self._link_src = None
        if src is None:
            return
        target = self._port_at(scene_pos)
        if target is not None and target.kind == "in":
            self.window.connect_nodes(src.node_name, target.node.node_name)

    def _node_at(self, view_pos):
        for item in self.items(view_pos):
            while item is not None:
                if isinstance(item, NodeItem):
                    return item
                item = item.parentItem()
        return None

    def mouseReleaseEvent(self, event):
        self._panning = False
        self._blank_press = None
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._node_at(event.position().toPoint()) is None:
            self._blank_press = event.position()
            self._panning = False
            self.scene().clearSelection()
            event.accept()
            return
        self._blank_press = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._blank_press is not None:
            delta = event.position() - self._blank_press
            if not self._panning and delta.manhattanLength() > 6:
                self._panning = True
                self._pan_start = event.position()
            if self._panning and self._pan_start is not None:
                step = event.position() - self._pan_start
                self._pan_start = event.position()
                self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - step.x()))
                self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - step.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _port_at(self, scene_pos: QPointF):
        for item in self.scene().items(scene_pos):
            if isinstance(item, PortItem):
                return item
            if isinstance(item, NodeItem) and item.in_port is not None:
                return item.in_port
        return None

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self.sync_scene_rect()

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        type_name = event.mimeData().text()
        pos = self.mapToScene(event.position().toPoint())
        self.window.add_block_at(type_name, pos.x(), pos.y())
        event.acceptProposedAction()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            selected = [i for i in self.scene().selectedItems() if isinstance(i, NodeItem)]
            if selected:
                self.window.delete_node(selected[0].node_name)
                return
        super().keyPressEvent(event)

    def fit(self):
        if not self.nodes:
            return
        self.fitInView(self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
