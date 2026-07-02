from pathlib import Path


def test_dashboard_contains_system_diagnostics_panel_and_loader():
    for rel in ("dashboard.html", "frontend/dashboard.html"):
        html = Path(rel).read_text(encoding="utf-8")

        assert 'id="system-diagnostics-summary"' in html
        assert 'id="system-diagnostics-list"' in html
        assert "async function loadSystemDiagnostics()" in html
        assert "/api/system/diagnostics" in html
        assert "loadSystemDiagnostics();" in html

