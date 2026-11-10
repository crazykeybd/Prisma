from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from common.run_context import RunContext
from keywords.keyword_lib import execute_command
from keywords.keyword_store import (
    BUILTIN_ACTIONS,
    BUILTIN_NAMES,
    KEYWORD_FILE,
    KeywordError,
    all_keywords,
    delete_custom,
    find_custom,
    load_custom,
    normalize_keyword,
    save_custom,
)


def _fields():
    return [
        {"index": 0, "role": "dir", "label": "运行目录", "required": True},
        {"index": 2, "role": "object", "label": "可执行程序", "required": True},
        {"index": 3, "role": "param", "label": "输入卡", "required": False},
    ]


def _payload(**overrides):
    payload = {
        "name": "case_setup",
        "impl": "command",
        "command": "{exe} -i {p1} -o {dir}/out.txt",
        "object": "可执行程序",
        "params": "输入卡",
        "desc": "调用第三方求解器准备算例",
        "fields": _fields(),
    }
    payload.update(overrides)
    return payload


class KeywordStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        logger = logging.getLogger("prisma")
        for handler in list(logger.handlers):
            if getattr(handler, "_prisma_project_file", False):
                logger.removeHandler(handler)
                handler.close()
        self._tmp.cleanup()

    def test_save_creates_project_file_and_is_listed_after_builtins(self):
        keyword = save_custom(self.root, _payload())
        self.assertTrue(keyword["custom"])
        path = self.root / KEYWORD_FILE
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual([item["name"] for item in payload["keywords"]], ["case_setup"])

        catalog = all_keywords(self.root)
        self.assertEqual(len(catalog), len(BUILTIN_NAMES) + 1)
        self.assertFalse(catalog[0]["custom"])
        self.assertEqual(catalog[-1]["name"], "case_setup")
        self.assertEqual(catalog[-1]["source"], "自定义")
        for item in catalog[: len(BUILTIN_NAMES)]:
            self.assertIn(item["impl"], BUILTIN_ACTIONS)

    def test_rename_keeps_single_entry(self):
        save_custom(self.root, _payload())
        save_custom(self.root, _payload(name="case_setup_v2"), original_name="case_setup")
        names = [item["name"] for item in load_custom(self.root)]
        self.assertEqual(names, ["case_setup_v2"])
        self.assertIsNone(find_custom(self.root, "case_setup"))
        self.assertIsNotNone(find_custom(self.root, "case_setup_v2"))

    def test_duplicate_and_builtin_names_are_rejected(self):
        save_custom(self.root, _payload())
        with self.assertRaises(KeywordError):
            save_custom(self.root, _payload())
        with self.assertRaises(KeywordError):
            save_custom(self.root, _payload(name="run_software"))

    def test_rename_onto_existing_name_is_rejected(self):
        save_custom(self.root, _payload())
        save_custom(self.root, _payload(name="second_case"))
        with self.assertRaises(KeywordError):
            save_custom(self.root, _payload(name="second_case"), original_name="case_setup")

    def test_invalid_definitions_are_rejected(self):
        cases = [
            _payload(name=""),
            _payload(name="1bad name"),
            _payload(name="bad-name"),
            _payload(impl="no_such_impl"),
            _payload(command=""),
            _payload(command="{exe} {p9}"),
            _payload(desc=""),
            _payload(fields=[]),
            _payload(fields=[
                {"index": 1, "role": "param", "label": "参数"},
                {"index": 2, "role": "object", "label": "对象"},
            ]),
            _payload(fields=[
                {"index": 0, "role": "dir", "label": "运行目录"},
                {"index": 2, "role": "object", "label": ""},
            ]),
            _payload(fields=[
                {"index": 0, "role": "dir", "label": "运行目录"},
                {"index": 0, "role": "dir", "label": "重复列"},
                {"index": 2, "role": "object", "label": "对象"},
            ]),
            _payload(fields=[
                {"index": 0, "role": "dir", "label": "运行目录"},
                {"index": 2, "role": "object", "label": "对象"},
                {"index": 7, "role": "param", "label": "越界"},
            ]),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(KeywordError):
                    normalize_keyword(payload)

    def test_alias_impl_does_not_require_command(self):
        keyword = save_custom(
            self.root,
            _payload(impl="run_software", command="", name="run_mysolver"),
        )
        self.assertEqual(keyword["impl"], "run_software")
        self.assertEqual(keyword["command"], "")

    def test_delete_custom_and_reject_builtin_delete(self):
        save_custom(self.root, _payload())
        delete_custom(self.root, "case_setup")
        self.assertEqual(load_custom(self.root), [])
        with self.assertRaises(KeywordError):
            delete_custom(self.root, "case_setup")
        with self.assertRaises(KeywordError):
            delete_custom(self.root, "folder_prep")

    def test_load_skips_broken_entries_and_tolerates_bad_json(self):
        (self.root / KEYWORD_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(load_custom(self.root), [])
        with self.assertRaises(KeywordError):
            load_custom(self.root, strict=True)
        (self.root / KEYWORD_FILE).write_text(json.dumps({
            "version": 1,
            "keywords": [
                {"name": "ok_case", "fields": _fields(), "impl": "command",
                 "command": "{exe}", "desc": "ok"},
                {"name": "run_software", "fields": _fields(), "desc": "内置名"},
                {"name": "bad name", "fields": _fields(), "desc": "非法名"},
                {"name": "no_fields", "fields": [], "desc": "缺字段"},
            ],
        }), encoding="utf-8")
        self.assertEqual([item["name"] for item in load_custom(self.root)], ["ok_case"])


class CustomKeywordExecutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.config = self.root / "config.yaml"
        self.config.write_text("softwareType: relap5\n", encoding="utf-8")
        self.context = RunContext(root=self.root, config_path=self.config)
        (self.root / "test_cases" / "case_01").mkdir(parents=True)

    def tearDown(self):
        logger = logging.getLogger("prisma")
        for handler in list(logger.handlers):
            if getattr(handler, "_prisma_project_file", False):
                logger.removeHandler(handler)
                handler.close()
        self._tmp.cleanup()

    def test_command_template_is_rendered_and_run_in_case_dir(self):
        save_custom(self.root, _payload(
            name="run_third_party",
            command="{exe} -i {p1} -o out.txt # {dir}",
        ))
        with patch("keywords.keyword_lib.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="ok", stderr="")
            ok = execute_command(
                ["A-1", "case_01", "run_third_party", "solver.exe", "input.dat"],
                context=self.context,
            )
        self.assertTrue(ok)
        args, kwargs = run.call_args
        rendered = args[0]
        self.assertIn("solver.exe", rendered)
        self.assertIn("-i input.dat", rendered)
        self.assertTrue(kwargs["shell"])
        self.assertEqual(Path(kwargs["cwd"]), (self.root / "test_cases" / "case_01"))
        self.assertIn(str(self.root / "test_cases" / "case_01"), rendered)

    def test_command_template_failure_returns_false(self):
        save_custom(self.root, _payload(name="run_failing", command="{exe} {p1}"))
        with patch("keywords.keyword_lib.subprocess.run") as run:
            run.return_value = Mock(returncode=2, stdout="", stderr="boom")
            ok = execute_command(
                ["A-2", "case_01", "run_failing", "solver.exe", "input.dat"],
                context=self.context,
            )
        self.assertFalse(ok)

    def test_alias_forwards_to_builtin_action(self):
        save_custom(self.root, _payload(
            name="my_check", impl="check", command="", desc="别名检查",
        ))
        ok = execute_command(
            ["A-3", "case_01", "my_check", "任何对象", "说明"],
            context=self.context,
        )
        self.assertTrue(ok)
        log_text = (self.root / "log" / "prisma_run.log").read_text(
            encoding="utf-8", errors="ignore")
        self.assertIn("check args=", log_text)

    def test_unknown_action_still_fails(self):
        ok = execute_command(
            ["A-4", "case_01", "not_a_keyword", "obj"],
            context=self.context,
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
