from decafclaw.skills.vault._sections import Document, Section


def test_document_round_trip():
    text = "# Title\n\nBody line.\n\n## Sub\n\n- item\n"
    doc = Document.from_text(text)
    assert doc.to_text() == text


def test_section_walk_by_path():
    text = "# Top\n\n## Child\n\ncontent\n"
    doc = Document.from_text(text)
    sec = doc.find_section("top/child")
    assert sec is not None
    assert sec.normalized_title == "child"
