"""Verify autounattend XMLs carry the Win11 system-requirement bypass.

Checks all Win11 autounattend templates: the generic base.xml and the
per-product overrides (win11-24h2.xml, win11-25h2.xml, etc). All must
write the LabConfig DWORDs in the windowsPE pass.
"""
from pathlib import Path

from lxml import etree

NS = {"u": "urn:schemas-microsoft-com:unattend",
      "wcm": "http://schemas.microsoft.com/WMIConfig/2002/State"}


def _all_win11_xmls() -> list[Path]:
    here = Path(__file__).resolve().parent.parent / "autounattend"
    return [here / "base.xml"] + sorted(here.glob("win11-*.xml"))


def _has_labconfig_keys(tree) -> bool:
    paths = tree.xpath(
        './/u:settings[@pass="windowsPE"]/'
        'u:component[@name="Microsoft-Windows-Setup"]/'
        'u:RunSynchronousCommand/u:Path/text()',
        namespaces=NS,
    )
    pstrs = [str(c) for c in paths]
    return (
        any("BypassTPMCheck" in p for p in pstrs)
        and any("BypassSecureBootCheck" in p for p in pstrs)
        and any("BypassRAMCheck" in p for p in pstrs)
    )


def test_every_win11_autounattend_has_bypass_keys():
    files = _all_win11_xmls()
    assert files, "no autounattend XMLs found"
    missing = [f.name for f in files if not _has_labconfig_keys(etree.parse(str(f)))]
    assert not missing, f"missing bypass LabConfig keys in: {missing}"


def test_labconfig_keys_write_to_correct_path():
    tree = etree.parse(str(_all_win11_xmls()[0]))
    paths = tree.xpath(
        './/u:settings[@pass="windowsPE"]/'
        'u:component[@name="Microsoft-Windows-Setup"]/'
        'u:RunSynchronousCommand/u:Path/text()',
        namespaces=NS,
    )
    for p in paths:
        assert 'HKLM\\SYSTEM\\Setup\\LabConfig' in str(p), p
        assert str(p).startswith("reg add "), p


def test_bypass_orders_are_correct():
    tree = etree.parse(str(_all_win11_xmls()[0]))
    cmds = tree.xpath(
        './/u:settings[@pass="windowsPE"]/'
        'u:component[@name="Microsoft-Windows-Setup"]/'
        'u:RunSynchronousCommand',
        namespaces=NS,
    )
    orders = [int(c.find("u:Order", NS).text) for c in cmds]
    assert orders == sorted(orders), f"RunSynchronousCommand orders not sequential: {orders}"
