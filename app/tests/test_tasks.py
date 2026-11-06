import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["TASKS_TABLE"] = "test-tasks-table"
os.environ["ENVIRONMENT"] = "test"
os.environ["APPLICATION_NAME"] = "task-api"

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)

import health
import tasks
from tasks import (
    _create_task,
    _delete_task,
    _get_task,
    _list_tasks,
    _update_task,
    handler,
)


class TestHealthHandler(unittest.TestCase):

    def test_returns_200(self):
        result = health.handler({}, None)
        self.assertEqual(result["statusCode"], 200)

    def test_body_has_required_fields(self):
        result = health.handler({}, None)
        body = json.loads(result["body"])
        self.assertEqual(body["status"], "healthy")
        self.assertIn("timestamp", body)
        self.assertIn("environment", body)
        self.assertIn("service", body)

    def test_environment_matches_env_var(self):
        result = health.handler({}, None)
        body = json.loads(result["body"])
        self.assertEqual(body["environment"], "test")


class TestHandlerRouting(unittest.TestCase):

    @patch("tasks.table")
    def test_routes_get_list(self, mock_table):
        mock_table.scan.return_value = {"Items": [], "Count": 0, "ScannedCount": 0}
        event = {"httpMethod": "GET", "pathParameters": None, "queryStringParameters": None}
        result = handler(event, None)
        self.assertEqual(result["statusCode"], 200)

    @patch("tasks.table")
    def test_routes_get_single(self, mock_table):
        mock_table.get_item.return_value = {
            "Item": {"task_id": "abc", "title": "T", "status": "pending"}
        }
        event = {"httpMethod": "GET", "pathParameters": {"task_id": "abc"},
                 "queryStringParameters": None}
        result = handler(event, None)
        self.assertEqual(result["statusCode"], 200)

    def test_options_returns_200(self):
        event = {"httpMethod": "OPTIONS", "pathParameters": None, "queryStringParameters": None}
        result = handler(event, None)
        self.assertEqual(result["statusCode"], 200)

    def test_unknown_method_returns_405(self):
        event = {"httpMethod": "PATCH", "pathParameters": None, "queryStringParameters": None}
        result = handler(event, None)
        self.assertEqual(result["statusCode"], 405)

class TestCreateTask(unittest.TestCase):

    @patch("tasks.table")
    def test_success_minimal(self, mock_table):
        mock_table.put_item.return_value = {}
        event = {"body": json.dumps({"title": "Buy groceries"})}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 201)
        body = json.loads(result["body"])
        self.assertEqual(body["title"], "Buy groceries")
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["priority"], "medium")
        self.assertIn("task_id", body)
        self.assertIn("created_at", body)

    @patch("tasks.table")
    def test_success_full(self, mock_table):
        mock_table.put_item.return_value = {}
        event = {
            "body": json.dumps({
                "title": "Deploy fix",
                "description": "Hotfix for auth bug",
                "status": "in_progress",
                "priority": "critical",
                "user_id": "user-42",
                "tags": ["backend", "hotfix"],
                "due_date": "2026-11-10",
            })
        }
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 201)
        body = json.loads(result["body"])
        self.assertEqual(body["priority"], "critical")
        self.assertEqual(body["user_id"], "user-42")
        self.assertEqual(body["tags"], ["backend", "hotfix"])

    def test_missing_title_returns_400(self):
        event = {"body": json.dumps({"description": "No title given"})}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 400)

    def test_empty_title_returns_400(self):
        event = {"body": json.dumps({"title": "   "})}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 400)

    def test_invalid_status_returns_400(self):
        event = {"body": json.dumps({"title": "Task", "status": "flying"})}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 400)

    def test_invalid_priority_returns_400(self):
        event = {"body": json.dumps({"title": "Task", "priority": "super-urgent"})}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 400)

    def test_null_body_returns_400(self):
        event = {"body": None}
        result = _create_task(event)
        self.assertEqual(result["statusCode"], 400)


class TestGetTask(unittest.TestCase):

    @patch("tasks.table")
    def test_found(self, mock_table):
        mock_table.get_item.return_value = {
            "Item": {"task_id": "abc-123", "title": "Found it", "status": "done"}
        }
        result = _get_task("abc-123")
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body["title"], "Found it")

    @patch("tasks.table")
    def test_not_found_returns_404(self, mock_table):
        mock_table.get_item.return_value = {}
        result = _get_task("nonexistent")
        self.assertEqual(result["statusCode"], 404)

class TestListTasks(unittest.TestCase):

    @patch("tasks.table")
    def test_scan_all(self, mock_table):
        mock_table.scan.return_value = {
            "Items": [{"task_id": "1"}, {"task_id": "2"}],
            "Count": 2,
            "ScannedCount": 2,
        }
        result = _list_tasks({"queryStringParameters": None})
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body["count"], 2)

    @patch("tasks.table")
    def test_query_by_user_id(self, mock_table):
        mock_table.query.return_value = {
            "Items": [{"task_id": "1", "user_id": "u1"}],
            "Count": 1,
        }
        result = _list_tasks({"queryStringParameters": {"user_id": "u1"}})
        self.assertEqual(result["statusCode"], 200)
        mock_table.query.assert_called_once()

    @patch("tasks.table")
    def test_empty_result(self, mock_table):
        mock_table.scan.return_value = {"Items": [], "Count": 0, "ScannedCount": 0}
        result = _list_tasks({"queryStringParameters": {}})
        body = json.loads(result["body"])
        self.assertEqual(body["tasks"], [])

class TestUpdateTask(unittest.TestCase):

    @patch("tasks.table")
    def test_update_status(self, mock_table):
        mock_table.update_item.return_value = {
            "Attributes": {"task_id": "abc", "status": "done", "updated_at": "2026-11-01"}
        }
        event = {"body": json.dumps({"status": "done"})}
        result = _update_task("abc", event)
        self.assertEqual(result["statusCode"], 200)

    @patch("tasks.table")
    def test_update_multiple_fields(self, mock_table):
        mock_table.update_item.return_value = {
            "Attributes": {
                "task_id": "abc",
                "title": "New title",
                "priority": "high",
                "updated_at": "2026-11-01",
            }
        }
        event = {"body": json.dumps({"title": "New title", "priority": "high"})}
        result = _update_task("abc", event)
        self.assertEqual(result["statusCode"], 200)

    def test_no_valid_fields_returns_400(self):
        event = {"body": json.dumps({"task_id": "hack", "created_at": "overwrite"})}
        result = _update_task("abc", event)
        self.assertEqual(result["statusCode"], 400)

    def test_invalid_status_in_update_returns_400(self):
        event = {"body": json.dumps({"status": "flying"})}
        result = _update_task("abc", event)
        self.assertEqual(result["statusCode"], 400)


class TestDeleteTask(unittest.TestCase):

    @patch("tasks.table")
    def test_delete_success(self, mock_table):
        mock_table.delete_item.return_value = {}
        result = _delete_task("abc-123")
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertIn("deleted", body["message"])


class TestResponseFormat(unittest.TestCase):

    @patch("tasks.table")
    def test_cors_headers_present(self, mock_table):
        mock_table.put_item.return_value = {}
        event = {"body": json.dumps({"title": "CORS test"})}
        result = _create_task(event)
        self.assertIn("Access-Control-Allow-Origin", result["headers"])
        self.assertEqual(result["headers"]["Access-Control-Allow-Origin"], "*")

    @patch("tasks.table")
    def test_body_is_json_string(self, mock_table):
        mock_table.scan.return_value = {"Items": [], "Count": 0, "ScannedCount": 0}
        result = _list_tasks({"queryStringParameters": None})
        self.assertIsInstance(result["body"], str)
        json.loads(result["body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
