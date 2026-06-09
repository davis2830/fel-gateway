"""SAT Guatemala DTE XML builder (FEL v0.2 namespace).

Ported from ``taller_mecanico/facturacion/services/xml_dte.py`` and
generalized: it accepts a neutral ``InvoiceCreate`` payload + a ``Tenant``
record (issuer) and produces the unsigned XML. Signing is the
certificador's job.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from xml.dom import minidom
from xml.etree import ElementTree as ET

from app.db.models import Tenant
from app.domain.schemas import InvoiceCreate

NS_DTE = "http://www.sat.gob.gt/dte/fel/0.2.0"
ET.register_namespace("dte", NS_DTE)

_TWO = Decimal("0.01")


def _d(value: Decimal | float | int | None) -> str:
    """Format with 2 decimals, half-up rounding (SAT-friendly)."""
    if value is None:
        return "0.00"
    return f"{Decimal(value).quantize(_TWO, rounding=ROUND_HALF_UP):.2f}"


def _strip_nit(nit: str | None) -> str:
    if not nit:
        return "CF"
    return str(nit).replace("-", "").replace(" ", "").upper() or "CF"


def build_xml_dte(payload: InvoiceCreate, tenant: Tenant) -> str:
    """Return a pretty-printed XML DTE string for the given payload+tenant.

    Per-line tax computation uses ``payload.tasa_iva`` and ``iva_incluido``;
    the global ``Totales`` block uses caller-supplied ``totales``.
    """
    tasa = Decimal(payload.tasa_iva)
    iva_incluido = payload.iva_incluido

    root = ET.Element(f"{{{NS_DTE}}}GTDocumento", attrib={"Version": "0.1"})
    sat = ET.SubElement(root, f"{{{NS_DTE}}}SAT", attrib={"ClaseDocumento": "dte"})
    dte = ET.SubElement(sat, f"{{{NS_DTE}}}DTE", attrib={"ID": "DatosCertificados"})
    datos_emision = ET.SubElement(dte, f"{{{NS_DTE}}}DatosEmision", attrib={"ID": "DatosEmision"})

    # ── DatosGenerales ─────────────────────────────────────────────
    fecha_emision = payload.fecha_emision.isoformat(timespec="seconds")
    ET.SubElement(
        datos_emision,
        f"{{{NS_DTE}}}DatosGenerales",
        attrib={
            "Tipo": payload.tipo,
            "FechaHoraEmision": fecha_emision,
            "CodigoMoneda": payload.moneda,
        },
    )

    # ── Emisor (tenant) ────────────────────────────────────────────
    emisor = ET.SubElement(
        datos_emision,
        f"{{{NS_DTE}}}Emisor",
        attrib={
            "NITEmisor": _strip_nit(tenant.nit),
            "NombreEmisor": tenant.legal_name,
            "CodigoEstablecimiento": str(tenant.establishment_code),
            "NombreComercial": tenant.commercial_name or tenant.legal_name,
            "AfiliacionIVA": tenant.affiliation_iva,
        },
    )
    direccion_emisor = ET.SubElement(emisor, f"{{{NS_DTE}}}DireccionEmisor")
    ET.SubElement(direccion_emisor, f"{{{NS_DTE}}}Direccion").text = tenant.address
    ET.SubElement(direccion_emisor, f"{{{NS_DTE}}}CodigoPostal").text = tenant.postal_code
    ET.SubElement(direccion_emisor, f"{{{NS_DTE}}}Municipio").text = tenant.municipio
    ET.SubElement(direccion_emisor, f"{{{NS_DTE}}}Departamento").text = tenant.departamento
    ET.SubElement(direccion_emisor, f"{{{NS_DTE}}}Pais").text = tenant.country

    # ── Receptor ───────────────────────────────────────────────────
    nit_rec = _strip_nit(payload.receptor.nit)
    receptor = ET.SubElement(
        datos_emision,
        f"{{{NS_DTE}}}Receptor",
        attrib={
            "IDReceptor": nit_rec,
            "NombreReceptor": payload.receptor.nombre or "Consumidor Final",
            "TipoEspecial": "CF" if nit_rec == "CF" else "",
        },
    )
    direccion_receptor = ET.SubElement(receptor, f"{{{NS_DTE}}}DireccionReceptor")
    ET.SubElement(direccion_receptor, f"{{{NS_DTE}}}Direccion").text = payload.receptor.direccion
    ET.SubElement(direccion_receptor, f"{{{NS_DTE}}}CodigoPostal").text = payload.receptor.postal_code
    ET.SubElement(direccion_receptor, f"{{{NS_DTE}}}Municipio").text = payload.receptor.municipio
    ET.SubElement(direccion_receptor, f"{{{NS_DTE}}}Departamento").text = payload.receptor.departamento
    ET.SubElement(direccion_receptor, f"{{{NS_DTE}}}Pais").text = payload.receptor.country

    # ── Items ──────────────────────────────────────────────────────
    items_el = ET.SubElement(datos_emision, f"{{{NS_DTE}}}Items")

    for idx, it in enumerate(payload.items, start=1):
        cantidad = Decimal(it.cantidad)
        pu_bruto = Decimal(it.precio_unitario)
        descuento = Decimal(it.descuento)

        if iva_incluido and tasa > 0:
            pu_sin_iva = (pu_bruto / (Decimal("1") + tasa)).quantize(_TWO, rounding=ROUND_HALF_UP)
        else:
            pu_sin_iva = pu_bruto

        total_sin_iva = (pu_sin_iva * cantidad - descuento).quantize(_TWO, rounding=ROUND_HALF_UP)
        if total_sin_iva < 0:
            total_sin_iva = Decimal("0")
        iva_item = (total_sin_iva * tasa).quantize(_TWO, rounding=ROUND_HALF_UP)

        item_elem = ET.SubElement(
            items_el,
            f"{{{NS_DTE}}}Item",
            attrib={"BienOServicio": it.bien_o_servicio, "NumeroLinea": str(idx)},
        )
        ET.SubElement(item_elem, f"{{{NS_DTE}}}Cantidad").text = _d(cantidad)
        ET.SubElement(item_elem, f"{{{NS_DTE}}}UnidadMedida").text = it.unidad_medida or "UND"
        ET.SubElement(item_elem, f"{{{NS_DTE}}}Descripcion").text = (it.descripcion or "")[:200]
        ET.SubElement(item_elem, f"{{{NS_DTE}}}PrecioUnitario").text = _d(pu_sin_iva)
        ET.SubElement(item_elem, f"{{{NS_DTE}}}Precio").text = _d(total_sin_iva + iva_item)
        ET.SubElement(item_elem, f"{{{NS_DTE}}}Descuento").text = _d(descuento)

        impuestos_item = ET.SubElement(item_elem, f"{{{NS_DTE}}}Impuestos")
        impuesto_item = ET.SubElement(impuestos_item, f"{{{NS_DTE}}}Impuesto")
        ET.SubElement(impuesto_item, f"{{{NS_DTE}}}NombreCorto").text = "IVA"
        ET.SubElement(impuesto_item, f"{{{NS_DTE}}}CodigoUnidadGravable").text = "1"
        ET.SubElement(impuesto_item, f"{{{NS_DTE}}}MontoGravable").text = _d(total_sin_iva)
        ET.SubElement(impuesto_item, f"{{{NS_DTE}}}MontoImpuesto").text = _d(iva_item)

        ET.SubElement(item_elem, f"{{{NS_DTE}}}Total").text = _d(total_sin_iva + iva_item)

    # ── Totales ────────────────────────────────────────────────────
    totales = ET.SubElement(datos_emision, f"{{{NS_DTE}}}Totales")
    imp_totales = ET.SubElement(totales, f"{{{NS_DTE}}}TotalImpuestos")
    ET.SubElement(
        imp_totales,
        f"{{{NS_DTE}}}TotalImpuesto",
        attrib={
            "NombreCorto": "IVA",
            "TotalMontoImpuesto": _d(payload.totales.monto_iva),
        },
    )
    ET.SubElement(totales, f"{{{NS_DTE}}}GranTotal").text = _d(payload.totales.total)

    # ── Complemento NCRE (referencia documento original) ──────────
    if payload.tipo == "NCRE" and payload.ncre_reference:
        ref_orig = payload.ncre_reference
        complementos = ET.SubElement(datos_emision, f"{{{NS_DTE}}}Complementos")
        complemento = ET.SubElement(
            complementos,
            f"{{{NS_DTE}}}Complemento",
            attrib={
                "IDComplemento": "ReferenciasNota",
                "NombreComplemento": "Referencias Nota de Crédito",
                "URIComplemento": "#ReferenciasNota",
            },
        )
        ET.SubElement(
            complemento,
            f"{{{NS_DTE}}}ReferenciasNota",
            attrib={
                "NumeroAutorizacionDocumentoOrigen": ref_orig.uuid_sat or "",
                "SerieDocumentoOrigen": ref_orig.serie or "",
                "NumeroDocumentoOrigen": ref_orig.numero or "",
                "FechaEmisionDocumentoOrigen": ref_orig.fecha_emision_original.isoformat(
                    timespec="seconds"
                ),
                "MotivoAjuste": ref_orig.motivo,
            },
        )

    xml_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return minidom.parseString(xml_bytes).toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
