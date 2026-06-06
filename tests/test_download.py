from unittest.mock import patch, MagicMock
import pytest
from scripts.uupd.download import build_request, parse_response, ConversionInputs

UUID = "00000000-0000-0000-0000-000000000001"

def test_build_request_shapes_query():
    url = build_request(UUID, edition="professional", lang="en-US")
    assert "uupdump.net" in url
    assert UUID in url
    assert "professional" in url.lower() or "edition=" in url.lower()

def test_parse_response_extracts_file_list():
    fake_html = """
    <html><body>
      <a href="/files/test1.cab">test1.cab</a>
      <a href="/files/test2.esd">test2.esd</a>
      <a href="/files/uup_convert.sh">convert</a>
    </body></html>
    """
    parsed = parse_response(fake_html)
    assert "test1.cab" in parsed.files
    assert "test2.esd" in parsed.files
    assert parsed.converter_script_url
