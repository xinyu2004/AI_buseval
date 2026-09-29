"""Keep GUI language tests off the machine's saved preference."""
import pytest


@pytest.fixture(autouse=True)
def isolated_ui_language(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSEVAL_UI_SETTINGS", str(tmp_path / "ui.ini"))
    from buseval.gui.i18n import set_lang

    set_lang("zh")
    yield
    set_lang("zh")
