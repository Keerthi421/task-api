"""
Health check endpoint - GET /health
Returns service status, environment, and timestamp.
"""

import json
import os
from datetime import datetime, timezone


def handler(event: dict, context) -> dict:
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "status": "healthy",
            "service": os.environ.get("APPLICATION_NAME", "task-api"),
            "environment": os.environ.get("ENVIRONMENT", "unknown"),
            "version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "table": os.environ.get("TASKS_TABLE", "unknown"),
        }),
    }
