from utils.group_email_attachments_store import is_safe_attachment_stored_path


def test_is_safe_attachment_stored_path_accepts_subdir():
    assert is_safe_attachment_stored_path("data/group_email_attach/1/2/00_x.pdf")


def test_is_safe_attachment_stored_path_rejects_escape():
    assert not is_safe_attachment_stored_path("data/group_email_attach/../../../etc/passwd")
    assert not is_safe_attachment_stored_path("")
