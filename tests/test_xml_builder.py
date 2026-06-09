"""XML builder regression tests."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

from app.db.models import Tenant
from app.domain.schemas import (
    InvoiceCreate,
    InvoiceItem,
    InvoiceReceiver,
    InvoiceTotals,
    NCREReference,
)
from app.domain.xml_builder import NS_DTE, build_xml_dte


def _build_tenant(**overrides) -> Tenant:
    base = dict(
        id=uuid.uuid4(),
        name="demo",
        api_key_hash="x",
        nit="12345678",
        legal_name="Empresa Demo S.A.",
        commercial_name="Demo",
        address="Av Reforma 1-23",
        postal_code="01009",
        municipio="GUATEMALA",
        departamento="GUATEMALA",
        country="GT",
        affiliation_iva="GEN",
        establishment_code="1",
        serie_fel="A",
        contact_email="x@y.gt",
        provider="mock",
        provider_config={},
        ambiente="PRUEBAS",
        is_active=True,
    )
    base.update(overrides)
    return Tenant(**base)


def _payload(**overrides) -> InvoiceCreate:
    base = dict(
        tipo="FACT",
        fecha_emision=datetime(2026, 5, 28, 10, 30, 0),
        moneda="GTQ",
        iva_incluido=True,
        tasa_iva=Decimal("0.12"),
        receptor=InvoiceReceiver(
            nit="98765432",
            nombre="Cliente Demo",
            direccion="Calle 5",
            email="cliente@x.com",
        ),
        items=[
            InvoiceItem(
                descripcion="Huevos blancos cartón",
                cantidad=Decimal("2"),
                precio_unitario=Decimal("100"),
                bien_o_servicio="B",
                unidad_medida="UND",
            )
        ],
        totales=InvoiceTotals(monto_iva=Decimal("21.43"), total=Decimal("200")),
    )
    base.update(overrides)
    return InvoiceCreate(**base)


def test_build_xml_minimum_structure() -> None:
    tenant = _build_tenant()
    xml = build_xml_dte(_payload(), tenant)

    assert xml.startswith("<?xml")
    root = ET.fromstring(xml.split("\n", 1)[1] if xml.startswith("<?xml") else xml)
    # Strip declaration and reparse the body
    root = ET.fromstring(xml)

    # Namespace stickiness
    assert root.tag == f"{{{NS_DTE}}}GTDocumento"

    # Emisor
    emisor = root.find(
        f".//{{{NS_DTE}}}Emisor"
    )
    assert emisor is not None
    assert emisor.attrib["NITEmisor"] == "12345678"
    assert emisor.attrib["NombreEmisor"] == "Empresa Demo S.A."
    assert emisor.attrib["AfiliacionIVA"] == "GEN"

    # Receptor
    receptor = root.find(f".//{{{NS_DTE}}}Receptor")
    assert receptor is not None
    assert receptor.attrib["IDReceptor"] == "98765432"
    assert receptor.attrib["TipoEspecial"] == ""

    # GranTotal matches caller-supplied total
    grand_total = root.find(f".//{{{NS_DTE}}}GranTotal")
    assert grand_total is not None
    assert grand_total.text == "200.00"


def test_consumidor_final_marks_tipoespecial() -> None:
    tenant = _build_tenant()
    payload = _payload(receptor=InvoiceReceiver(nombre="Anónimo"))
    xml = build_xml_dte(payload, tenant)
    root = ET.fromstring(xml)
    rec = root.find(f".//{{{NS_DTE}}}Receptor")
    assert rec is not None
    assert rec.attrib["IDReceptor"] == "CF"
    assert rec.attrib["TipoEspecial"] == "CF"


def test_iva_incluido_strips_iva_from_unit_price() -> None:
    """For iva_incluido=True the per-line PrecioUnitario must be net of IVA."""
    tenant = _build_tenant()
    # 1 unit at 112 with 12% iva incluido => net unit price is 100.00
    payload = _payload(
        items=[
            InvoiceItem(
                descripcion="Servicio",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("112"),
                bien_o_servicio="S",
            )
        ],
        totales=InvoiceTotals(monto_iva=Decimal("12"), total=Decimal("112")),
    )
    xml = build_xml_dte(payload, tenant)
    root = ET.fromstring(xml)
    pu = root.find(f".//{{{NS_DTE}}}PrecioUnitario")
    assert pu is not None and pu.text == "100.00"
    monto_imp = root.find(f".//{{{NS_DTE}}}MontoImpuesto")
    assert monto_imp is not None and monto_imp.text == "12.00"


def test_iva_no_incluido_keeps_unit_price() -> None:
    tenant = _build_tenant()
    payload = _payload(
        iva_incluido=False,
        items=[
            InvoiceItem(
                descripcion="Servicio",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                bien_o_servicio="S",
            )
        ],
        totales=InvoiceTotals(monto_iva=Decimal("12"), total=Decimal("112")),
    )
    xml = build_xml_dte(payload, tenant)
    root = ET.fromstring(xml)
    pu = root.find(f".//{{{NS_DTE}}}PrecioUnitario")
    assert pu is not None and pu.text == "100.00"
    monto_imp = root.find(f".//{{{NS_DTE}}}MontoImpuesto")
    assert monto_imp is not None and monto_imp.text == "12.00"


def test_ncre_includes_complemento_referenciasnota() -> None:
    tenant = _build_tenant()
    payload = _payload(
        tipo="NCRE",
        ncre_reference=NCREReference(
            uuid_sat="ABC-UUID-ORIG",
            serie="A",
            numero="1234",
            fecha_emision_original=datetime(2026, 4, 1, 9, 0, 0),
            motivo="Devolución de producto",
        ),
    )
    xml = build_xml_dte(payload, tenant)
    root = ET.fromstring(xml)
    ref = root.find(f".//{{{NS_DTE}}}ReferenciasNota")
    assert ref is not None
    assert ref.attrib["NumeroAutorizacionDocumentoOrigen"] == "ABC-UUID-ORIG"
    assert ref.attrib["SerieDocumentoOrigen"] == "A"
    assert ref.attrib["MotivoAjuste"] == "Devolución de producto"


def test_nit_normalization_strips_dashes() -> None:
    tenant = _build_tenant()
    payload = _payload(receptor=InvoiceReceiver(nit="987-6543-2", nombre="Cliente"))
    xml = build_xml_dte(payload, tenant)
    root = ET.fromstring(xml)
    rec = root.find(f".//{{{NS_DTE}}}Receptor")
    assert rec is not None
    assert rec.attrib["IDReceptor"] == "98765432"
