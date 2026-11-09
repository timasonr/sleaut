"""Hard product boundaries. Do not implement anything listed here.

This tool researches *public* Google Maps / Places listings.
It is not a lead-gen outreach product.

Forbidden (intentionally unimplemented, do not add later):
- sending, queueing, or scheduling messages to businesses or owners
- WhatsApp / Telegram / SMS / email outreach, mail-merge, or "contact this lead"
- generating or storing cold-outreach / sales-pitch copy
- harvesting personal owner identities beyond what a public maps listing already shows
- scraping behind logins, CAPTCHAs, or non-public pages

Allowed data: business name, public category, public address, public phone
and website *as shown on the listing*, rating, photo count, Maps URL,
and obvious public-profile completeness signals (missing photos / website / hours).
"""

FORBIDDEN_FEATURES = (
    "outreach_send",
    "outreach_queue",
    "outreach_schedule",
    "whatsapp",
    "sms",
    "email_campaign",
    "mail_merge",
    "contact_this_lead",
    "sales_pitch",
    "cold_outreach_copy",
    "owner_identity_harvest",
    "login_scraping",
    "captcha_bypass",
)

# CSV / API export allowlist — public listing fields only.
PUBLIC_LISTING_FIELDS = (
    "id",
    "name",
    "category",
    "city",
    "address",
    "public_phone",
    "website",
    "rating",
    "ratings_count",
    "photos_count",
    "maps_url",
    "maps_profile_exists",
    "maps_status",
    "has_website",
    "has_phone",
    "has_photos",
    "has_hours",
    "has_posts",
    "business_status",
    "source",
    "lat",
    "lng",
)
