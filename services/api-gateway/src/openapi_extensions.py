from typing import Any

def create_postman_script(var_name: str, field_name: str) -> dict[str, Any]:
    """Create a Postman test script to set an environment variable."""
    return {
        "listen": "test",
        "script": {
            "type": "text/javascript",
            "exec": [
                "var jsonData = pm.response.json();",
                f'if (jsonData.{field_name}) {{',
                f'    pm.environment.set("{var_name}", jsonData.{field_name});',
                f'    console.log("Set environment variable {var_name}");',
                "}"
            ]
        }
    }

def add_postman_extensions(openapi_schema: dict[str, Any]) -> dict[str, Any]:
    """Inject Postman extensions into the OpenAPI schema."""
    paths = openapi_schema.get("paths", {})

    # Iterate all paths to find matches and inject scripts
    for path, methods in paths.items():
        for method, operation in methods.items():
            if method.lower() != "post":
                continue
            
            # 1. Partner API Key Generation
            if "partners" in path and path.endswith("/api-keys"):
                _inject_script(operation, "partner-api-key", "api_key")
            
            # 2. Tenant API Key Generation
            elif "tenants" in path and path.endswith("/api-keys"):
                _inject_script(operation, "tenant-api-key", "api_key")

            # 3. User Token Exchange
            elif path.endswith("/auth/token"):
                _inject_script(operation, "user-access-token", "access_token")

            # 4. User Token Refresh
            elif path.endswith("/auth/refresh"):
                _inject_script(operation, "user-access-token", "access_token")

    return openapi_schema

def _inject_script(operation: dict[str, Any], var_name: str, field_name: str) -> None:
    """Helper to inject the script into the operation."""
    if "x-postman-event" not in operation:
        operation["x-postman-event"] = []
    
    operation["x-postman-event"].append(
        create_postman_script(var_name, field_name)
    )
