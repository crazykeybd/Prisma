"""Tests for the LOCUST transient-table output parser."""

from __future__ import annotations

import unittest

from software import GenericCommandAdapter
from software.locust_output import extract_value, volume_number_from_key


SAMPLE_OUTPUT = """
Time = 1.00342 sec, dt = 0.01 sec, istep = 126

===> volume information:
  vol.no.     pressure    voidf    tempf
               (Pa)                 (K)
      201       673968  1.00000   639.93
===> junction information:
      201     20102     30101  1.00000

Time = 1000 sec, dt = 0.00657823 sec, istep = 100026

===> volume information:
  vol.no.     pressure    voidf    tempf
               (Pa)                 (K)
      201       649026  1.00000   593.15
  vol.no.     pressure    voidf    tempf
               (Pa)                 (K)
      301       612242  1.00000   593.15
===> junction information:
      201     20102     30101  1.00000
===> fwfc information:
      201       999999
"""


class LocustOutputTests(unittest.TestCase):
    def test_pressure_key_maps_to_locust_volume(self):
        self.assertEqual(volume_number_from_key("P@002010000"), 201)
        self.assertEqual(volume_number_from_key("p@109010000"), 10901)
        self.assertIsNone(volume_number_from_key("tempf@002010000"))
        self.assertIsNone(volume_number_from_key("P@002010001"))

    def test_reads_last_volume_table_not_junction_or_following_table(self):
        self.assertEqual(
            extract_value(SAMPLE_OUTPUT, "P@002010000"),
            "649026",
        )

    def test_missing_volume_or_section_returns_none(self):
        self.assertIsNone(extract_value(SAMPLE_OUTPUT, "P@004010000"))
        self.assertIsNone(extract_value("201 649026\n", "P@002010000"))

    def test_generic_adapter_dispatches_manifest_output_parser(self):
        adapter = GenericCommandAdapter.from_manifest(
            {
                "software_key": "locust",
                "display_name": "LOCUST",
                "arguments": ["-i", "{input}", "-o", "{output}"],
                "input_filenames": ["input.i"],
                "output_filename": "output",
                "output_parser": "locust",
            }
        )
        self.assertEqual(
            adapter.extract_value(SAMPLE_OUTPUT, "P@002010000"),
            "649026",
        )


if __name__ == "__main__":
    unittest.main()
