"""Tests for country/city detection and alias table."""
from django.test import TestCase

from core.search.countries import detect_countries, detect_country, nearby_suggestions


class DetectCountriesTests(TestCase):
    def test_remote_is_its_own_category(self):
        self.assertEqual(detect_countries("Remote"), ["Remote"])
        self.assertEqual(detect_countries("Remote, Worldwide"), ["Remote"])

    def test_kenyan_cities_match_kenya(self):
        self.assertEqual(detect_countries("Nairobi"), ["Kenya"])
        self.assertEqual(detect_countries("Mombasa, Kenya"), ["Kenya"])
        self.assertEqual(detect_countries("Kisumu"), ["Kenya"])

    def test_multi_location_counts_each_country(self):
        location = "New York, NY, London, Toronto"
        countries = detect_countries(location)
        self.assertIn("United States", countries)
        self.assertIn("United Kingdom", countries)
        self.assertIn("Canada", countries)

    def test_cloudflare_locations(self):
        # Cloudflare commonly lists multiple offices in one location field.
        location = "Austin, TX, United States; London, United Kingdom; Singapore"
        countries = detect_countries(location)
        self.assertIn("United States", countries)
        self.assertIn("United Kingdom", countries)
        self.assertIn("Singapore", countries)

    def test_spotify_locations(self):
        location = "Stockholm, Sweden; New York, NY; Berlin, Germany"
        countries = detect_countries(location)
        self.assertIn("Sweden", countries)
        self.assertIn("United States", countries)
        self.assertIn("Germany", countries)

    def test_detect_country_returns_first_match(self):
        self.assertEqual(detect_country("Nairobi, Kenya"), "Kenya")
        self.assertEqual(detect_country("Remote"), "Remote")
        self.assertIsNone(detect_country("Unknown Galaxy"))

    def test_nearby_suggestions_for_kenya(self):
        suggestions = nearby_suggestions("Kenya")
        self.assertIn("Uganda", suggestions)
        self.assertIn("Tanzania", suggestions)
