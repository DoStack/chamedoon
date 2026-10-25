import re

from django.db import migrations, models

# Old property types that were rental modes, and the mode each one becomes.
LEGACY_MODES = {"sublet": "sublet", "lease_transfer": "lease_transfer", "short_stay": "short_term"}

# Kept here, not imported from home.alerts, so the migration does not change when the app code does.
ROOM_WORDS = re.compile(
    r"\b(?:shared|room in|roommate|room for|wg|wg zimmer|chambre|colocation|zimmer in|zimmer zur)\b"
    r"|اتاق(?!\s*خواب)|هم[‌ ]?خانه|هم[‌ ]?خونه",
    re.IGNORECASE,
)
STUDIO_WORDS = re.compile(r"\b(?:studio|bachelor|1[\s-]?zi(?:mmer)?|einzimmer)\b|استودیو|استدیو", re.IGNORECASE)
BASEMENT_WORDS = re.compile(r"\b(?:basement|souterrain)\b|بیسمنت|بیس‌منت|زیرزمین", re.IGNORECASE)
HOUSE_WORDS = re.compile(r"\b(?:house|townhouse|haus|reihenhaus|maison)\b|تاون[‌ ]?هاوس|ویلایی", re.IGNORECASE)
HOME_WORDS = re.compile(
    r"\b(?:suite|apartment|apt|flat|condo|\d\s*(?:-|\s)?bed(?:room)?s?|junior|wohnung|appartement|logement"
    r"|\d\s*(?:-|\s)?zi(?:mmer)?|\d\s*(?:1/2|½))\b"
    r"|آپارتمان|سوئیت|سوییت|واحد|خوابه|اتاق\s*خواب|کاندو",
    re.IGNORECASE,
)


def infer_type(listing) -> str:
    """The kind of place behind an old sublet, lease transfer or short stay, read from its text; else other."""
    review = listing.post.review_json if isinstance(listing.post.review_json, dict) else {}
    text = " ".join(
        str(part)
        for part in (listing.summary_fa, listing.summary_en, review.get("summary_fa"), review.get("summary_en"), listing.post.text)
        if part
    )
    if ROOM_WORDS.search(text):
        return "room"
    if STUDIO_WORDS.search(text):
        return "studio"
    if BASEMENT_WORDS.search(text):
        return "basement"
    if HOUSE_WORDS.search(text):
        return "house"
    if HOME_WORDS.search(text) or listing.rooms:
        return "apartment"
    return "other"


def infer_mode(listing) -> str:
    """Rental mode of a listing that already had a real property type, from what the old code read at run time."""
    flags = listing.flags if isinstance(listing.flags, dict) else {}
    if listing.price_period in {"night", "week"}:
        return "short_term"
    if flags.get("untermiete") is True or flags.get("sublet") is True:
        return "sublet"
    if (listing.min_term_months or 0) >= 12 or flags.get("first_last") is True:
        return "long_term"
    return "unknown"


def split_type_and_mode(apps, schema_editor):
    HomeListing = apps.get_model("home", "HomeListing")
    for listing in HomeListing.objects.select_related("post").iterator(chunk_size=500):
        if listing.property_type in LEGACY_MODES:
            listing.rental_mode = LEGACY_MODES[listing.property_type]
            listing.property_type = infer_type(listing)
        else:
            listing.rental_mode = infer_mode(listing)
            if listing.rental_mode == "unknown":
                continue
        listing.save(update_fields=["property_type", "rental_mode"])


def merge_back(apps, schema_editor):
    """Reverse: a sublet, lease transfer or short stay becomes that property type again (the inferred type is lost)."""
    HomeListing = apps.get_model("home", "HomeListing")
    for old, mode in LEGACY_MODES.items():
        HomeListing.objects.filter(rental_mode=mode).update(property_type=old)


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0020_alert_wizard"),
    ]

    operations = [
        migrations.AddField(
            model_name="homelisting",
            name="rental_mode",
            field=models.CharField(
                choices=[
                    ("long_term", "Long-term lease"),
                    ("sublet", "Sublet (Zwischenmiete, Untermiete)"),
                    ("lease_transfer", "Lease transfer (Nachmieter)"),
                    ("short_term", "Short stay (nights or weeks)"),
                    ("unknown", "Not stated"),
                ],
                db_index=True,
                default="unknown",
                max_length=16,
            ),
        ),
        migrations.RunPython(split_type_and_mode, merge_back),
        migrations.AlterField(
            model_name="homelisting",
            name="property_type",
            field=models.CharField(
                choices=[
                    ("room", "Room in a shared home"),
                    ("studio", "Studio"),
                    ("apartment", "Apartment"),
                    ("house", "House"),
                    ("basement", "Basement unit"),
                    ("other", "Other"),
                ],
                default="other",
                help_text="What kind of place; bedrooms are in rooms.",
                max_length=24,
            ),
        ),
    ]
