import pytest

from home.full_text import MASK, find_text, full_text, mask_contact, tidy

PHONES = [
    "+1 416 555 0199",
    "416-555-0199",
    "(416) 555 0199",
    "۴۱۶ ۵۵۵ ۰۱۹۹",
    "٤١٦-٥٥٥-٠١٩٩",
    "۰۹۱۲۳۴۵۶۷۸۹",
    "+49 176 12345678",
    "0176 1234567",
    "4165550199",
    "۴۱۶۵۵۵۰۱۹۹",
]


@pytest.mark.parametrize("phone", PHONES)
def test_phone_in_any_digit_set_or_spacing_is_masked(phone):
    out = mask_contact(f"اتاق برای اجاره تماس: {phone} ممنون")
    assert MASK in out
    assert not any(ch.isdigit() for ch in out), out


@pytest.mark.parametrize(
    "contact",
    ["@sara_home", "＠sara_home", "@ab", "t.me/sara_home", "https://t.me/+AbCdEf12", "wa.me/14165550199", "http://x.example/y", "www.t.me/sara", "sara@mail.com", "SARA.K+rent@gmail.co.uk"],
)
def test_handles_links_and_emails_are_masked(contact):
    out = mask_contact(f"پیام بدید {contact} لطفا")
    assert MASK in out
    assert "sara" not in out.lower() and "@" not in out and "wa.me" not in out and "http" not in out


def test_known_bare_handle_is_masked():
    assert "ali" not in mask_contact("contact ali please", ("ali",)).replace(MASK, "")


def test_normal_numbers_and_places_stay():
    text = "اتاق ۱۲ متری، ۹۵۰ دلار، Yonge St، طبقه ۳، 2BR, M5V 2T6"
    assert mask_contact(text) == text


def test_adjacent_contacts_become_one_mask():
    out = mask_contact("416 555 0199 @sara_home")
    assert out.count(MASK) == 1


def test_tidy_cleans_for_reading():
    raw = "🔥🔥🔥 اتاق آماده 🔥🔥\n\n\n#اجاره #تورنتو\n----------\nقیمت ۹۵۰\n\n"
    assert tidy(raw) == "🔥 اتاق آماده 🔥\n\nقیمت ۹۵۰"


def test_full_text_masks_then_tidies_and_never_leaks():
    out = full_text("اتاق مبله\n☎️ ۰۹۱۲ ۳۴۵ ۶۷۸۹\nتلگرام @sara_home\nt.me/sara_home")
    assert "09" not in out and "۰۹" not in out and "sara" not in out
    assert MASK in out and out.startswith("اتاق مبله")


def test_find_text_is_one_short_line_without_masks():
    out = find_text("اتاق مبله نزدیک مترو\n\n" + "کلمه " * 100 + "\n0176 1234567")
    assert "\n" not in out and MASK not in out and len(out) <= 160 and out.startswith("اتاق مبله")
