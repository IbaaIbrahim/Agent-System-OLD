import json
import uuid
import sys
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Add src to python path to import app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.main import app

class PostmanCollectionGenerator:
    def __init__(self, openapi_schema: Dict[str, Any]):
        self.openapi = openapi_schema
        self.collection = {
            "info": {
                "name": self.openapi.get("info", {}).get("title", "API Collection"),
                "description": self.openapi.get("info", {}).get("description", ""),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
            },
            "item": []
        }
        self.folders: Dict[str, Dict[str, Any]] = {}

    def _resolve_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve $ref in schema."""
        if "$ref" in schema:
            ref_path = schema["$ref"]
            # ref_path is like "#/components/schemas/User"
            parts = ref_path.split("/")
            if parts[0] == "#":
                current = self.openapi
                for part in parts[1:]:
                    current = current.get(part, {})
                return self._resolve_schema(current)
        return schema

    def _generate_example(self, schema: Dict[str, Any]) -> Any:
        """Generate an example value based on the schema."""
        schema = self._resolve_schema(schema)
        
        if "example" in schema:
            return schema["example"]
        
        if "default" in schema:
            return schema["default"]

        schema_type = schema.get("type")
        
        # Handle allOf, anyOf, oneOf (simplification: take first)
        if "allOf" in schema:
            combined = {}
            for sub_schema in schema["allOf"]:
                resolved_sub = self._generate_example(sub_schema)
                if isinstance(resolved_sub, dict):
                    combined.update(resolved_sub)
            return combined
            
        if "anyOf" in schema:
            return self._generate_example(schema["anyOf"][0])
            
        if "oneOf" in schema:
            return self._generate_example(schema["oneOf"][0])

        if schema_type == "object":
            example = {}
            properties = schema.get("properties", {})
            for prop_name, prop_schema in properties.items():
                example[prop_name] = self._generate_example(prop_schema)
            return example

        elif schema_type == "array":
            items_schema = schema.get("items", {})
            return [self._generate_example(items_schema)]

        elif schema_type == "string":
            if "enum" in schema:
                return schema["enum"][0]
            if schema.get("format") == "date-time":
                return "2024-01-01T00:00:00Z"
            if schema.get("format") == "uuid":
                return str(uuid.uuid4())
            return "string"

        elif schema_type == "integer":
            return 0

        elif schema_type == "number":
            return 0.0

        elif schema_type == "boolean":
            return False

        return None

    def _get_or_create_folder(self, tag: str) -> Dict[str, Any]:
        if tag not in self.folders:
            folder = {
                "name": tag,
                "item": []
            }
            self.folders[tag] = folder
            self.collection["item"].append(folder)
        return self.folders[tag]

    def _convert_operation_to_item(self, path: str, method: str, operation: Dict[str, Any]) -> Dict[str, Any]:
        # Convert OpenAPI path parameters {param} to Postman syntax :param
        postman_path = path.replace("{", "{{").replace("}", "}}")
        
        item = {
            "name": operation.get("summary", f"{method.upper()} {path}"),
            "event": operation.get("x-postman-event", []),
            "request": {
                "method": method.upper(),
                "header": [],
                "url": {
                    "raw": f"{{{{base_url}}}}{postman_path}",
                    "host": ["{{base_url}}"],
                    "path": [p for p in postman_path.split("/") if p],
                    "variable": []
                }
            }
        }

        # Handle URL parameters (e.g., {id})
        for param in operation.get("parameters", []):
            if param.get("in") == "path":
                var_name = param["name"]
                item["request"]["url"]["variable"].append({
                    "key": var_name,
                    "value": "",
                    "description": param.get("description", "")
                })
            elif param.get("in") == "query":
                if "query" not in item["request"]["url"]:
                    item["request"]["url"]["query"] = []
                item["request"]["url"]["query"].append({
                    "key": param["name"],
                    "value": "",
                    "description": param.get("description", ""),
                    "disabled": not param.get("required", False)
                })

        # Handle request body
        if "requestBody" in operation:
            content = operation["requestBody"].get("content", {})
            if "application/json" in content:
                item["request"]["header"].append({
                    "key": "Content-Type",
                    "value": "application/json"
                })
                
                schema = content["application/json"].get("schema")
                if schema:
                    example_body = self._generate_example(schema)
                    item["request"]["body"] = {
                        "mode": "raw",
                        "raw": json.dumps(example_body, indent=2)
                    }
                else:
                    item["request"]["body"] = {
                        "mode": "raw",
                        "raw": "{}" 
                    }

        # Custom Auth Injection based on Path
        auth_header = None
        
        if path.startswith("/api/v1/auth/token"):
            # Token Exchange requires Tenant API Key
            auth_header = {"key": "Authorization", "value": "Bearer {{tenant-api-key}}"}
            
        elif path.startswith("/api/v1/users"):
            # User management requires Tenant API Key
            auth_header = {"key": "Authorization", "value": "Bearer {{tenant-api-key}}"}
            
        elif path.startswith("/api/v1/"):
            # Other v1 endpoints (chat, jobs, etc) require User Access Token
            # Exception: refresh endpoint also needs token (which is consistent)
            auth_header = {"key": "Authorization", "value": "Bearer {{user-access-token}}"}
            
        elif path.startswith("/api/partner/"):
            # Partner endpoints require Partner API Key
            auth_header = {"key": "Authorization", "value": "Bearer {{partner-api-key}}"}
            
        elif path.startswith("/api/admin/tenants"):
            # Tenant management (create/list) can be done by Partner
            auth_header = {"key": "Authorization", "value": "Bearer {{partner-api-key}}"}

        if auth_header:
            item["request"]["header"].append(auth_header)

        # Add generic headers
        item["request"]["header"].append({
            "key": "Accept",
            "value": "application/json"
        })

        return item

    def generate(self) -> Dict[str, Any]:
        paths = self.openapi.get("paths", {})
        
        for path, methods in paths.items():
            for method, operation in methods.items():
                if method.lower() not in ["get", "post", "put", "delete", "patch"]:
                    continue
                
                # Determine folder based on first tag, or use 'Default'
                tags = operation.get("tags", ["Default"])
                main_tag = tags[0] if tags else "Default"
                
                folder = self._get_or_create_folder(main_tag)
                
                item = self._convert_operation_to_item(path, method, operation)
                folder["item"].append(item)
        
        return self.collection

if __name__ == "__main__":
    openapi_schema = app.openapi()
    
    # Debug: Check if x-postman-event is present in the schema for key endpoints
    # This ensures our openapi_extensions injection worked before generation
    # print(json.dumps(openapi_schema, indent=2)) 

    generator = PostmanCollectionGenerator(openapi_schema)
    collection = generator.generate()
    
    print(json.dumps(collection, indent=2))
