from antrack.core.instruments.powermeter_client import PowermeterClient


def test_extract_power_from_text_parses_standard_pattern():
    text = "Power= -105.26 [dBm]      Ref=   0.00[dBm]"
    assert PowermeterClient.extract_power_from_text(text) == -105.26


def test_extract_power_from_text_parses_fallback_pattern():
    text = "Level -42.5 [dBm] extra"
    assert PowermeterClient.extract_power_from_text(text) == -42.5


def test_extract_power_from_text_handles_missing_value():
    assert PowermeterClient.extract_power_from_text("") is None
