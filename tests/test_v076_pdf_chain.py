from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from research_hub.zotero import pdf_attach
from research_hub.zotero.pdf_attach import PdfAttachPlan, attach_pdfs, find_pdf_url, plan_attach_for_items


def test_arxiv_source_first_priority(monkeypatch):
    monkeypatch.setattr(
        "research_hub.zotero.pdf_attach.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no network")),
    )

    url, source = find_pdf_url(arxiv_id="2501.12345", doi="10.48550/arXiv.2501.12345")

    assert source == "arxiv"
    assert url == "https://arxiv.org/pdf/2501.12345.pdf"


def test_openalex_oa_url_secondary(monkeypatch):
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "best_oa_location": {"pdf_url": "https://example.com/oa.pdf"},
    }
    monkeypatch.setattr("research_hub.zotero.pdf_attach.requests.get", lambda *args, **kwargs: response)

    url, source = find_pdf_url(doi="10.1016/j.x.2025.001")

    assert source == "openalex-oa"
    assert url == "https://example.com/oa.pdf"


def test_crossref_fallback_after_openalex_and_missing_unpaywall_email(monkeypatch, capsys):
    pdf_attach._HINT_SHOWN = False

    def fake_get(url, params=None, timeout=15):
        del timeout
        if url.startswith(pdf_attach.OPENALEX_BASE):
            return SimpleNamespace(ok=False)
        if url.startswith(pdf_attach.CROSSREF_BASE):
            return SimpleNamespace(
                ok=True,
                json=lambda: {
                    "message": {
                        "link": [
                            {"content-type": "application/pdf", "URL": "https://example.com/crossref.pdf"}
                        ]
                    }
                },
            )
        raise AssertionError((url, params))

    monkeypatch.setattr("research_hub.zotero.pdf_attach.requests.get", fake_get)

    url, source = find_pdf_url(doi="10.1000/crossref-only")

    assert source == "crossref-link"
    assert url == "https://example.com/crossref.pdf"
    err = capsys.readouterr().err
    assert "config set unpaywall_email" in err


def test_unpaywall_used_when_email_available(monkeypatch):
    def fake_get(url, params=None, timeout=15):
        del timeout
        if url.startswith(pdf_attach.OPENALEX_BASE):
            return SimpleNamespace(ok=False)
        if url.startswith(pdf_attach.UNPAYWALL_BASE):
            assert params == {"email": "user@example.com"}
            return SimpleNamespace(
                ok=True,
                json=lambda: {"best_oa_location": {"url_for_pdf": "https://example.com/unpaywall.pdf"}},
            )
        raise AssertionError(url)

    monkeypatch.setattr("research_hub.zotero.pdf_attach.requests.get", fake_get)

    url, source = find_pdf_url(doi="10.1000/upw", unpaywall_email="user@example.com")

    assert source == "unpaywall"
    assert url == "https://example.com/unpaywall.pdf"


def test_plan_attach_for_items_adds_publisher_fallback(monkeypatch):
    monkeypatch.setattr(
        "research_hub.zotero.pdf_attach.find_pdf_url",
        lambda doi="", arxiv_id="", **kwargs: ("", ""),
    )

    plans = plan_attach_for_items(
        [
            {
                "key": "ITEM1",
                "data": {
                    "title": "Paper",
                    "DOI": "10.1000/one",
                    "url": "https://publisher.example.com/paper",
                },
            }
        ],
        include_publisher_link=True,
    )

    assert plans[0].source == "publisher-page"
    assert plans[0].publisher_url == "https://publisher.example.com/paper"


def test_attach_pdfs_skips_items_with_existing_pdf():
    zot = MagicMock()
    zot.children.return_value = [
        {"data": {"itemType": "attachment", "contentType": "application/pdf"}}
    ]

    results = attach_pdfs(
        zot,
        [
            PdfAttachPlan(
                item_key="ITEM1",
                title="Paper",
                doi="10.1000/one",
                arxiv_id="",
                pdf_url="https://example.com/full.pdf",
                source="crossref-link",
            )
        ],
        rate_limit_rps=999.0,
    )

    assert results == {"ITEM1": "skip:already-has-pdf"}
    zot.create_items.assert_not_called()

