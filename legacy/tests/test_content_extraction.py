"""Content and business-signal extraction tests — no internet."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from content_extraction import extract_business_signals, extract_page_evidence

FIXTURE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Rozina Boutique</title>
  <meta name="description" content="Women's fashion boutique in Bandra, Mumbai.">
  <script>var secret = "do-not-extract";</script>
  <style>body { color: red; }</style>
</head>
<body>
  <nav><a href="/menu">Skip this nav</a></nav>
  <div id="cookie-banner">Accept cookies</div>
  <h1>Rozina Women's Boutique</h1>
  <h2>Visit us</h2>
  <p>Welcome to our store. Email hello@rozina.example or call +91 98765 43210.</p>
  <p>12 Linking Road, Bandra West, Mumbai 400050</p>
  <a href="/about">About</a>
  <a href="/contact">Contact</a>
  <a href="https://www.instagram.com/rozinaboutique">Instagram</a>
  <a href="https://wa.me/919876543210">WhatsApp</a>
  <a href="mailto:hello@rozina.example">Email us</a>
  <noscript>noscript noise</noscript>
</body>
</html>
"""


class ContentExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = extract_page_evidence(
            FIXTURE_HTML,
            source_url="https://www.rozina.example/home",
            final_url="https://rozina.example/",
        )
        self.signals = extract_business_signals(self.evidence)

    def test_title_extraction(self) -> None:
        self.assertEqual(self.evidence.title, "Rozina Boutique")

    def test_meta_description_extraction(self) -> None:
        self.assertEqual(
            self.evidence.meta_description,
            "Women's fashion boutique in Bandra, Mumbai.",
        )

    def test_visible_text_extraction(self) -> None:
        self.assertIn("Welcome to our store", self.evidence.text)
        self.assertIn("Rozina Women's Boutique", self.evidence.text)

    def test_scripts_and_styles_removed(self) -> None:
        self.assertNotIn("do-not-extract", self.evidence.text)
        self.assertNotIn("color: red", self.evidence.text)
        self.assertNotIn("noscript noise", self.evidence.text)
        self.assertNotIn("Skip this nav", self.evidence.text)
        self.assertNotIn("Accept cookies", self.evidence.text)

    def test_absolute_link_extraction(self) -> None:
        urls = {link.normalized_url or link.url for link in self.evidence.links}
        self.assertTrue(any(url.endswith("/about") for url in urls))
        self.assertTrue(any("instagram.com/rozinaboutique" in (url or "") for url in urls))
        about = next(link for link in self.evidence.links if "about" in (link.normalized_url or ""))
        self.assertTrue(about.url.startswith("http"))
        self.assertTrue(about.useful)

    def test_email_extraction(self) -> None:
        self.assertIn("hello@rozina.example", self.signals.emails)

    def test_phone_extraction(self) -> None:
        joined = " ".join(self.signals.phones)
        self.assertIn("98765", joined)

    def test_instagram_url_extraction(self) -> None:
        self.assertTrue(
            any("instagram.com/rozinaboutique" in url for url in self.signals.social_urls)
        )

    def test_whatsapp_url_extraction(self) -> None:
        self.assertTrue(any("wa.me/919876543210" in url for url in self.signals.whatsapp_urls))

    def test_address_and_city_signals(self) -> None:
        joined = " ".join(self.signals.address_candidates)
        self.assertIn("Linking Road", joined)
        self.assertIn("400050", joined)
        self.assertIn("Mumbai", self.signals.city_mentions)
        self.assertIn("Bandra", self.signals.city_mentions)

    def test_ecommerce_unit_price_is_not_an_address(self) -> None:
        evidence = extract_page_evidence(
            "<html><body><p>Unit price 18,000.00 per item. Quick View.</p></body></html>",
            source_url="https://shop.example",
            final_url="https://shop.example",
        )
        self.assertEqual(extract_business_signals(evidence).address_candidates, [])

    def test_currency_product_text_is_not_an_address(self) -> None:
        evidence = extract_page_evidence(
            "<html><body><p>$25,700.00 LIGHT GOLD PRE ST COLLECTION</p></body></html>",
            source_url="https://shop.example",
            final_url="https://shop.example",
        )
        self.assertEqual(extract_business_signals(evidence).address_candidates, [])


    def test_jsonld_localbusiness_and_og_site_name(self) -> None:
        html = """
        <html><head>
          <meta property="og:site_name" content="Rozina">
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": "Rozina",
            "telephone": "+91 22 1234 5678",
            "email": "hello@rozina.example",
            "sameAs": ["https://instagram.com/rozina"],
            "address": {
              "@type": "PostalAddress",
              "streetAddress": "12 Linking Road",
              "addressLocality": "Mumbai",
              "addressRegion": "MH",
              "postalCode": "400050"
            }
          }
          </script>
        </head><body><h1>Order Summary</h1></body></html>
        """
        evidence = extract_page_evidence(
            html, source_url="https://rozina.example", final_url="https://rozina.example"
        )
        self.assertEqual(evidence.og_site_name, "Rozina")
        sources = {fact.source for fact in evidence.structured_facts}
        self.assertIn("jsonld_localbusiness", sources)
        self.assertIn("og_site_name", sources)
        fields = {(fact.field, fact.value) for fact in evidence.structured_facts}
        self.assertIn(("name", "Rozina"), fields)
        self.assertIn(("telephone", "+91 22 1234 5678"), fields)
        self.assertIn(("email", "hello@rozina.example"), fields)
        self.assertIn(("addressLocality", "Mumbai"), fields)
        signals = extract_business_signals(evidence)
        self.assertIn("hello@rozina.example", signals.emails)
        self.assertTrue(any("instagram.com/rozina" in url for url in signals.social_urls))
        self.assertIn("Mumbai", signals.city_mentions)


if __name__ == "__main__":
    unittest.main()
