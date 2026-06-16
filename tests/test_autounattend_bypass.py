"""Verify autounattend/base.xml carries the Win11 system-requirement bypass."""
from pathlib import Path

from lxml import etree

BASE_XML = Path(__file__).resolve().parent.parent / "autounattend" / "base.xml"
NS = {"u": "urn:schemas-microsoft-com:unattend",
      "wcm": "http://schemas.microsoft.com/WMIConfig/2002/State"}


def test_base_xml_is_well_formed():
    tree = etree.parse(str(BASE_XML))
    root = tree.getroot()
    assert root.tag == f"{{{NS['u']}}}unattend"


def test_base_xml_has_setup_component_in_windowspe():
    tree = etree.parse(str(BASE_XML))
    setup = tree.xpath(
        './/u:settings[@pass="windowsPE"]/u:component[@name="Microsoft-Windows-Setup"]',
        namespaces=NS,
    )
    assert setup, "Microsoft-Windows-Setup component must exist in windowsPE pass"
    assert setup[0].get("processorArchitecture") == "amd64"


def test_base_xml_writes_all_three_labconfig_keys():
    tree = etree.parse(str(BASE_XML))
    cmds = tree.xpath(
        './/u:settings[@pass="windowsPE"]/'
        'u:component[@name="Microsoft-Windows-Setup"]/'
        'u:RunSynchronousCommand/u:Path/text()',
        namespaces=NS,
    )
    paths = [str(c) for c in cmds]
    assert any("BypassTPMCheck" in p for p in paths), paths
    assert any("BypassSecureBootCheck" in p for p in paths), paths
    assert any("BypassRAMCheck" in p for p in paths), paths
    for p in paths:
        assert 'HKLM\\SYSTEM\\Setup\\LabConfig' in p, p
        assert p.startswith("reg add "), p
