import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TASKS_TABLE"])

ALLOWED_STATUSES = {"pending", "in_progress", "done", "cancelled"}
ALLOWED_PRIORITIES = {"low", "medium", "high", "critical"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters") or {}
    task_id = path_params.get("task_id")

    try:
        if method == "OPTIONS":
            return api_response(200, {"message": "OK"})

        if method == "GET" and not task_id:
            return _list_tasks(event)
        if method == "GET" and task_id:
            return _get_task(task_id)
        if method == "POST":
            return _create_task(event)
        if method == "PUT" and task_id:
            return _update_task(task_id, event)
        if method == "DELETE" and task_id:
            return _delete_task(task_id)

        return api_response(405, {"error": f"Method {method} not allowed"})

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        print(f"[DynamoDB ClientError] {code}: {exc}")
        return api_response(500, {"error": "Database error", "detail": code})
    except Exception as exc:
        print(f"[Unhandled] {type(exc).__name__}: {exc}")
        return api_response(500, {"error": "Internal server error"})

def _list_tasks(event: dict) -> dict:
    qp = event.get("queryStringParameters") or {}
    user_id = qp.get("user_id")
    status = qp.get("status")
    limit = min(int(qp.get("limit", 50)), 100)

    if user_id:
        kwargs: dict = {
            "IndexName": "UserIdIndex",
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "Limit": limit,
        }
        if status:
            kwargs["KeyConditionExpression"] = (
                Key("user_id").eq(user_id) & Key("status").eq(status)
            )
        result = table.query(**kwargs)
    else:
        result = table.scan(Limit=limit)

    return api_response(200, {
        "tasks": result["Items"],
        "count": result["Count"],
        "scanned_count": result.get("ScannedCount", result["Count"]),
        "last_evaluated_key": result.get("LastEvaluatedKey"),
    })


def _get_task(task_id: str) -> dict:
    result = table.get_item(Key={"task_id": task_id})
    item = result.get("Item")
    if not item:
        return api_response(404, {"error": f"Task '{task_id}' not found"})
    return api_response(200, item)


def _create_task(event: dict) -> dict:
    body = parse_body(event)

    title = (body.get("title") or "").strip()
    if not title:
        return api_response(400, {"error": "'title' is required and must be non-empty"})

    status = body.get("status", "pending")
    if status not in ALLOWED_STATUSES:
        return api_response(400, {
            "error": f"Invalid status '{status}'",
            "allowed": sorted(ALLOWED_STATUSES),
        })

    priority = body.get("priority", "medium")
    if priority not in ALLOWED_PRIORITIES:
        return api_response(400, {
            "error": f"Invalid priority '{priority}'",
            "allowed": sorted(ALLOWED_PRIORITIES),
        })

    ts = now_iso()
    task = {
        "task_id": str(uuid.uuid4()),
        "title": title,
        "description": (body.get("description") or "").strip(),
        "status": status,
        "priority": priority,
        "user_id": (body.get("user_id") or "anonymous").strip(),
        "tags": body.get("tags") or [],
        "created_at": ts,
        "updated_at": ts,
    }

    if body.get("due_date"):
        task["due_date"] = body["due_date"]

    table.put_item(Item=task)
    return api_response(201, task)

def _update_task(task_id: str, event: dict) -> dict:
    body = parse_body(event)

    mutable_fields = {"title", "description", "status", "priority", "tags", "due_date"}
    updates = {k: v for k, v in body.items() if k in mutable_fields}

    if not updates:
        return api_response(400, {
            "error": "No updatable fields provided",
            "updatable": sorted(mutable_fields),
        })

    if "status" in updates and updates["status"] not in ALLOWED_STATUSES:
        return api_response(400, {
            "error": f"Invalid status '{updates['status']}'",
            "allowed": sorted(ALLOWED_STATUSES),
        })
    if "priority" in updates and updates["priority"] not in ALLOWED_PRIORITIES:
        return api_response(400, {
            "error": f"Invalid priority '{updates['priority']}'",
            "allowed": sorted(ALLOWED_PRIORITIES),
        })

    updates["updated_at"] = now_iso()

    expr_names = {"#tid": "task_id"}
    expr_values: dict = {}
    set_clauses: list = []

    for field, value in updates.items():
        safe_name = f"#f_{field}"
        safe_val = f":v_{field}"
        expr_names[safe_name] = field
        expr_values[safe_val] = value
        set_clauses.append(f"{safe_name} = {safe_val}")

    update_expression = "SET " + ", ".join(set_clauses)

    try:
        result = table.update_item(
            Key={"task_id": task_id},
            UpdateExpression=update_expression,
            ConditionExpression="attribute_exists(#tid)",
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
        return api_response(200, result["Attributes"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return api_response(404, {"error": f"Task '{task_id}' not found"})
        raise


def _delete_task(task_id: str) -> dict:
    try:
        table.delete_item(
            Key={"task_id": task_id},
            ConditionExpression="attribute_exists(#tid)",
            ExpressionAttributeNames={"#tid": "task_id"},
        )
        return api_response(200, {"message": f"Task '{task_id}' deleted successfully"})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return api_response(404, {"error": f"Task '{task_id}' not found"})
        raise
