"""Tests sin red: extract_json_value y response_matches_schema."""
import json
import unittest

from app.llm.schema_check import extract_json_value, response_matches_schema

SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


class ExtractJsonValueTests(unittest.TestCase):
    def test_plain_object_and_array(self):
        self.assertEqual(extract_json_value('{"ok": true}'), {"ok": True})
        self.assertEqual(extract_json_value("  [1, 2]  "), [1, 2])

    def test_markdown_fence(self):
        self.assertEqual(extract_json_value('```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(extract_json_value("```JSON\n{\"ok\": false}\n```"), {"ok": False})
        self.assertEqual(extract_json_value("```\n[1, 2, 3]\n```"), [1, 2, 3])

    def test_prose_around_json(self):
        self.assertEqual(extract_json_value('Here is the result:\n{"ok": false}'), {"ok": False})
        self.assertEqual(extract_json_value('{"ok": true}\nHope this helps.'), {"ok": True})
        self.assertEqual(extract_json_value("items follow:\n[1, 2, 3]\nthanks"), [1, 2, 3])

    def test_fence_with_inner_prose_falls_back_to_braces(self):
        text = '```\nnote: {"ok": true}\n```'
        self.assertEqual(extract_json_value(text), {"ok": True})

    def test_object_slice_tried_before_array_slice(self):
        # Misma prioridad que en producción: el primer {…} válido gana al […].
        self.assertEqual(extract_json_value('prefix [1, {"n": 2}] suffix'), {"n": 2})

    def test_array_slice_when_object_slice_is_invalid(self):
        self.assertEqual(extract_json_value("see [1, 2] and also {not json"), [1, 2])

    def test_empty_and_non_json(self):
        for raw in ("", "   ", "no json here", None):
            with self.assertRaises(json.JSONDecodeError):
                extract_json_value(raw)


class ResponseMatchesSchemaExtractTests(unittest.TestCase):
    def test_plain_json_unchanged(self):
        self.assertTrue(response_matches_schema('{"ok": true}', SCHEMA))
        self.assertFalse(response_matches_schema('{"ok": "yes"}', SCHEMA))

    def test_fenced_and_prose_match_schema(self):
        self.assertTrue(response_matches_schema('```json\n{"ok": true}\n```', SCHEMA))
        self.assertTrue(response_matches_schema('Sure:\n{"ok": true}\n', SCHEMA))

    def test_extracted_value_still_checked(self):
        self.assertFalse(response_matches_schema('```json\n{"ok": "yes"}\n```', SCHEMA))
        self.assertFalse(response_matches_schema('result: {"nope": true}', SCHEMA))
        self.assertFalse(response_matches_schema("```json\n[1]\n```", {"type": "object"}))
        self.assertTrue(response_matches_schema("```json\n[1, 2]\n```", {"type": "array"}))

    def test_unparseable_is_false(self):
        self.assertFalse(response_matches_schema("not json", SCHEMA))
        self.assertFalse(response_matches_schema("", SCHEMA))
        self.assertFalse(response_matches_schema(None, SCHEMA))

    def test_no_schema_uses_nonempty_text(self):
        self.assertTrue(response_matches_schema("plain prose", None))
        self.assertFalse(response_matches_schema("   ", None))


if __name__ == "__main__":
    unittest.main()
