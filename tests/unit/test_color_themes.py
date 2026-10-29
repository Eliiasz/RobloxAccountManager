import unittest

from features import color_themes as ct

LEGACY_DEFAULTS = {
    "BG": "#0E0E0E", "PANEL": "#151515", "INPUT": "#1A1A1A", "TEXT": "#EDEDED",
    "MUTED": "#AAAAAA", "LINE": "#242424", "SELECT": "#2A2A2A", "NOTE": "#D6BB7D",
    "ACCENT": "#0078D7",
    "HOVER": "#3A3A3A", "PRESSED": "#1E1E1E", "RAISED": "#2E2E2E",
    "ACCENT_HOVER": "#1A8FE0", "ACCENT_PRESSED": "#006DC4", "ACCENT_BRIGHT": "#3A7BD5",
    "ACCENT_SOFT": "#3A5A9A", "ACCENT_TEXT": "#5DBBFF", "ACCENT_DEEP": "#0A1A2A",
}


class ResolveColorsTests(unittest.TestCase):
    def test_new_users_get_mono_glass(self):
        self.assertEqual(ct.DEFAULT_THEME, "mono_glass")
        self.assertEqual(ct.resolve_colors({}), ct.resolve_colors({"color_theme": "mono_glass"}))

    def test_the_new_themes_are_listed(self):
        labels = dict(ct.theme_choices())
        self.assertEqual(labels["mono_glass"], "Mono Glass")
        self.assertEqual(labels["violet_glow"], "Violet Glow")
        self.assertEqual(labels["sky_paper"], "Sky Paper")
        self.assertTrue(ct.is_light("sky_paper"))
        self.assertFalse(ct.is_light("violet_glow"))

    def test_existing_themes_keep_their_colours(self):
        colors = ct.resolve_colors({"color_theme": "default"})
        self.assertEqual(colors["ACCENT"], "#FF7A1A")
        self.assertEqual(colors["BG"], "#0A0D13")
        self.assertEqual(ct.PRESETS["default"]["label"], "ANAne Orange")

    def test_classic_theme_matches_the_colours_the_app_used_before(self):
        colors = ct.resolve_colors({"color_theme": "classic"})
        for key, value in LEGACY_DEFAULTS.items():
            with self.subTest(key=key):
                self.assertEqual(colors[key], value)
        self.assertEqual(colors["ON_ACCENT"], "#FFFFFF")

    def test_missing_unknown_or_invalid_settings_fall_back_to_the_default(self):
        default = ct.resolve_colors({})
        for settings in (None, "text", {"color_theme": "no-such-theme"}, {"color_theme": 5},
                         {"color_theme": None}, {"accent_color": "red"}, {"accent_color": "#12345"},
                         {"accent_color": 7}):
            with self.subTest(settings=settings):
                self.assertEqual(ct.resolve_colors(settings), default)

    def test_every_preset_defines_every_key_as_a_valid_colour(self):
        for name in ct.PRESETS:
            colors = ct.resolve_colors({"color_theme": name})
            self.assertEqual(set(colors), set(ct.KEYS))
            for key, value in colors.items():
                with self.subTest(theme=name, key=key):
                    self.assertTrue(ct.is_hex_color(value), value)

    def test_presets_are_different_from_each_other(self):
        backgrounds = [ct.resolve_colors({"color_theme": name})["BG"] for name in ct.PRESETS]
        self.assertEqual(len(set(backgrounds)), len(backgrounds))

    def test_choices_start_with_the_default_and_have_labels(self):
        choices = ct.theme_choices()
        self.assertEqual(choices[0], ("mono_glass", "Mono Glass"))
        self.assertTrue(all(label for _, label in choices))

    def test_a_saved_custom_accent_is_ignored(self):
        self.assertEqual(
            ct.resolve_colors({"color_theme": "classic", "accent_color": "#e5557f"}),
            ct.resolve_colors({"color_theme": "classic"}),
        )

    def test_settings_are_not_modified(self):
        settings = {"color_theme": "nord", "accent_color": "#112233"}
        ct.resolve_colors(settings)
        self.assertEqual(settings, {"color_theme": "nord", "accent_color": "#112233"})


class ReadabilityTests(unittest.TestCase):
    def all_themes(self):
        for name in ct.PRESETS:
            yield name, ct.resolve_colors({"color_theme": name})

    def check(self, first, second, minimum):
        for name, colors in self.all_themes():
            with self.subTest(theme=name, pair=f"{first}/{second}"):
                self.assertGreaterEqual(ct.contrast_ratio(colors[first], colors[second]), minimum)

    def test_body_text_is_easy_to_read(self):
        for background in ("BG", "PANEL", "INPUT", "SELECT"):
            self.check("TEXT", background, 7.0)

    def test_muted_and_note_text_pass_the_normal_text_minimum(self):
        for background in ("BG", "PANEL", "INPUT"):
            self.check("MUTED", background, 4.5)
            self.check("NOTE", background, 4.5)

    def test_accent_text_is_readable_on_the_background(self):
        self.check("ACCENT_TEXT", "BG", 4.5)

    def test_text_on_accent_buttons_is_readable(self):
        self.check("ON_ACCENT", "ACCENT", 4.4)


class ColourMathTests(unittest.TestCase):
    def test_mix(self):
        self.assertEqual(ct.mix("#000000", "#FFFFFF", 0.5), "#808080")
        self.assertEqual(ct.mix("#102030", "#102030", 0.7), "#102030")
        self.assertEqual(ct.mix("#000000", "#FFFFFF", 0), "#000000")
        self.assertEqual(ct.mix("#000000", "#FFFFFF", 1), "#FFFFFF")

    def test_contrast_ratio_extremes(self):
        self.assertAlmostEqual(ct.contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(ct.contrast_ratio("#777777", "#777777"), 1.0, places=3)

    def test_is_hex_color(self):
        for good in ("#000000", "#aBcDeF"):
            self.assertTrue(ct.is_hex_color(good))
        for bad in ("000000", "#00000", "#0000000", "#GG0000", "", None, 5):
            self.assertFalse(ct.is_hex_color(bad))


if __name__ == "__main__":
    unittest.main()
