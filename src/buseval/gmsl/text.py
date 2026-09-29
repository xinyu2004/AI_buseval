"""GMSL breakdown text shared by the GUI and the CLI."""
from __future__ import annotations

from .calculator import FEC, coax_tier_rows, csi_rate
from ..gui.i18n import t


def gbps_label(mbps: float) -> str:
    gbps = round(mbps / 1000.0, 2)
    if abs(gbps - round(gbps, 1)) < 1e-9:
        return f"{gbps:.1f} Gbps"
    return f"{gbps:.2f} Gbps"


def factor_text(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def compact(value: float) -> str:
    return f"{value:g}"


def one(value: float) -> str:
    return f"{value:,.1f}"


def bandwidth_line(mbps: float) -> str:
    return t("gmsl_link_bw_value").format(mbps=f"{mbps:,.1f}", gbps=f"{mbps / 1000:.2f}")


def _display_width(text: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def align(rows: list[tuple[str, ...]]) -> str:
    """Pad each column so the factor and the result start on the same column."""
    width = max((len(row) for row in rows), default=0)
    padded = [row + ("",) * (width - len(row)) for row in rows]
    lines = []
    columns = [
        max((_display_width(row[index]) for row in padded), default=0)
        for index in range(width)
    ]
    for row in padded:
        parts = []
        for index, cell in enumerate(row):
            if index == width - 1:
                parts.append(cell)
            else:
                gap = columns[index] - _display_width(cell) + 2
                parts.append(cell + " " * gap)
        lines.append("".join(parts).rstrip())
    return "\n".join(lines)


def _pclk_expr(link) -> str:
    geom = f"{link.width}×{link.height}×{compact(link.fps)}"
    if link.heartbeat:
        geom += f" × {compact(link.blanking)}"
    return f"{geom} = {link.pclk_mhz:.3f} MHz"


def _apply(previous: float, factor: float, origin: str, result: float) -> str:
    """previous × factor, with the factor's source beside it."""
    return f"{one(previous)} × {factor_text(factor)}  {origin} = {result:,.1f} Mbps"


def link_steps(link) -> str:
    note = t("gmsl_heartbeat_on_note") if link.heartbeat else t("gmsl_heartbeat_off_note")
    bpp = compact(link.bpp_link)
    base = link.pclk_mhz * link.bpp_link
    bpp_note = f"  {t('gmsl_link_bpp').format(bpp=bpp)}" if link.bpp_link != link.bpp else ""
    crc_origin = f"({bpp} + {compact(link.crc_bpp)}) / {bpp}" if link.crc_bpp else f"({t('gmsl_fec_off')})"
    fec_origin = "(128/120)" if link.fec_enabled else f"({t('gmsl_fec_off')})"
    rows = [
        (t("gmsl_pclk"), f"{_pclk_expr(link)}  {note}"),
        (t("gmsl_pixel"), f"{link.pclk_mhz:.3f} × {bpp} = {one(base)} Mbps{bpp_note}"),
        (f"+ {t('gmsl_crc')}", _apply(base, link.crc_factor, crc_origin, link.after_crc_mbps)),
        (
            f"+ {t('gmsl_encoding')}",
            _apply(link.after_crc_mbps, link.encoding_factor, f"({link.encoding_expr})", link.after_encoding_mbps),
        ),
        (
            f"+ {t('gmsl_packet')}",
            _apply(link.after_encoding_mbps, link.packet_factor, "(2048/2047)", link.after_packet_mbps),
        ),
        (
            f"+ {t('gmsl_fec')}",
            _apply(link.after_packet_mbps, link.fec_factor, fec_origin, link.link_bw_mbps),
        ),
    ]
    if not link.fec_enabled:
        rows.append((
            "GMSL3",
            _apply(link.after_packet_mbps, FEC, "(128/120)", link.after_packet_mbps * FEC),
        ))
    return align(rows)


def coax_product(link) -> str:
    return " × ".join([
        f"{link.pclk_mhz:.3f}",
        compact(link.bpp_link),
        factor_text(link.crc_factor),
        factor_text(link.encoding_factor),
        factor_text(link.packet_factor),
        factor_text(link.fec_factor),
    ]) + f" = {link.link_bw_mbps:,.1f} Mbps"


def _shared_factor(factor: float, origin: str) -> str:
    return f"× {factor_text(factor)}  {origin}"


def coeff_lines(links) -> str:
    sample = links[0]
    fec_origin = "(128/120)" if sample.fec_enabled else f"({t('gmsl_fec_off')})"
    rows = []
    seen = set()
    for link in links:
        key = (link.bpp_link, link.crc_bpp)
        if key in seen:
            continue
        seen.add(key)
        bpp = compact(link.bpp_link)
        if link.crc_bpp:
            origin = f"({bpp} + {compact(link.crc_bpp)}) / {bpp}"
            rows.append((f"+ {t('gmsl_crc')}", _shared_factor(link.crc_factor, origin)))
        else:
            rows.append((f"+ {t('gmsl_crc')}", _shared_factor(1, f"({t('gmsl_fec_off')})")))
    rows.extend([
        (f"+ {t('gmsl_encoding')}", _shared_factor(sample.encoding_factor, f"({sample.encoding_expr})")),
        (f"+ {t('gmsl_packet')}", _shared_factor(sample.packet_factor, "(2048/2047)")),
        (f"+ {t('gmsl_fec')}", _shared_factor(sample.fec_factor, fec_origin)),
    ])
    if not sample.fec_enabled:
        packet = sum(link.after_packet_mbps for link in links)
        rows.append(("GMSL3", _apply(packet, FEC, "(128/120)", packet * FEC)))
    note = t("gmsl_heartbeat_on_note") if sample.heartbeat else t("gmsl_heartbeat_off_note")
    return note + "\n" + t("gmsl_coefficients") + "\n" + align(rows)


def multi_steps(links) -> str:
    camera_rows = []
    for link in links:
        note = t("gmsl_link_bpp").format(bpp=compact(link.bpp_link)) if link.bpp_link != link.bpp else ""
        camera_rows.append((link.name, f"{coax_product(link)}  {note}".rstrip()))
    coax = sum(link.link_bw_mbps for link in links)
    parts = " + ".join(one(link.link_bw_mbps) for link in links)
    camera_rows.append((t("gmsl_coax"), f"{parts} = {coax:,.1f} Mbps"))
    return align(camera_rows) + "\n\n" + coeff_lines(links)


def csi_text(payload, phy: str, lanes: int) -> str:
    """Result rows, padded with the same columns as the coax breakdown."""
    if payload is None:
        return ""
    rate = csi_rate(payload, phy, lanes)
    per_name = t("gmsl_per_trio") if rate["phy"] == "cphy" else t("gmsl_per_lane")
    per_value = t("gmsl_lane_value").format(
        rate=f"{rate['per_lane_gbps']:.3f}",
        step=f"{rate['config_gbps']:.1f}",
    )
    rows = [
        (t("gmsl_after_blank"), f"{payload:,.1f} Mbps"),
        (per_name, per_value),
    ]
    if rate["clock_mhz"] is not None:
        rows.append((t("gmsl_clock"), f"{rate['clock_mhz']:,.1f} MHz"))
    if rate["symbol_msym"] is not None:
        rows.append((t("gmsl_symbol"), t("gmsl_symbol_value").format(rate=f"{rate['symbol_msym']:,.1f}")))
    text = align(rows)
    if rate["symbol_msym"] is not None:
        text += "\n" + t("gmsl_cphy_hint")
    if rate["deskew"]:
        text += "\n" + t("gmsl_deskew")
    return text


def csi_block(payload: float, phy: str, lanes: int) -> str:
    rate = csi_rate(payload, phy, lanes)
    kind = t("gmsl_cphy") if rate["phy"] == "cphy" else t("gmsl_dphy")
    unit = t("gmsl_trio") if rate["phy"] == "cphy" else t("gmsl_lane")
    head = t("gmsl_phy_line").format(phy=kind, count=rate["lanes"], unit=unit)
    return head + "\n" + csi_text(payload, phy, lanes)


def tier_lines(links) -> list[tuple[str, float]]:
    """Aligned tier rows. Each line is colored by its occupancy."""
    rows, best = coax_tier_rows(links)
    source = []
    for row in rows:
        mark = t("best_fit") if row["tier"] == best else ""
        source.append((
            row["title"],
            gbps_label(row["capacity_mbps"]),
            f"{row['util'] * 100:.1f}%",
            mark,
        ))
    lines = align(source).splitlines() if source else []
    return [(lines[index], rows[index]["util"]) for index in range(len(rows))]
