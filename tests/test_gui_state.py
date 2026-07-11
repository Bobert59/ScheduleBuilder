from __future__ import annotations

import unittest

from schedule_builder.gui_state import new_config, normalize_config, rule_label, suggested_output_name


class GuiStateTests(unittest.TestCase):
    def test_new_config_contains_every_editable_section(self) -> None:
        config = new_config()
        self.assertIn("schedule", config)
        self.assertIn("default_rules", config)
        self.assertIn("quality_weights", config)
        self.assertIn("solver", config)
        self.assertEqual(config["doctors"], [])

    def test_normalize_preserves_existing_values_and_adds_gui_defaults(self) -> None:
        config = normalize_config(
            {
                "schedule": {"start": "2026-09-21", "end": "2026-10-18"},
                "custom_future_field": {"keep": True},
                "doctors": [{"name": "Dr. Example"}],
            }
        )
        self.assertTrue(config["custom_future_field"]["keep"])
        self.assertEqual(config["doctors"][0]["time_off"], [])
        self.assertIn("weekend_single", config["quality_weights"])

    def test_output_name_and_rule_summary_are_human_readable(self) -> None:
        config = new_config()
        config["schedule"] = {"start": "2026-09-21", "end": "2026-10-18"}
        self.assertEqual(suggested_output_name(config), "Sep21_Oct18_Schedule.xlsx")
        self.assertEqual(
            rule_label({"type": "allowed_weekdays", "weekdays": ["Thu", "Fri"]}),
            "Allowed weekdays: Thu, Fri",
        )


if __name__ == "__main__":
    unittest.main()
