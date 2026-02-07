import os
import json
from typing import Any, Dict

from jsonschema import ValidationError, validate
from openai import OpenAI

from app.utils.logging_configuration import get_logger

logger = get_logger(__name__)


def _schema_path() -> str:
    # Resolve: src/checklist_generator/ -> ../constants/data/gpt_json_schema.json
    base_dir = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(base_dir, "..", "constants", "data", "schema.json"))


def _load_schema() -> Dict[str, Any]:
    path = _schema_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Checklist JSON schema not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _openai_client() -> OpenAI:
    # api_key = os.getenv("OPENAI_API_KEY")
    # if not api_key:
    #     raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY")
        )


def generate_checklist_with_chatgpt(user_request: str | None = None) -> dict:
    client = _openai_client()
    schema = _load_schema()

    # Log the received user_request for debugging
    logger.info(
        f"generate_checklist_with_chatgpt called with user_request: {user_request[:200] if user_request else 'None'} (length: {len(user_request) if user_request else 0})"
    )

    # Validate user_request - reject empty or None values
    if not user_request or user_request.strip() == "" or user_request.strip().lower() == "null":
        error_msg = "user_request is required and cannot be empty, None, or 'Null'. A valid checklist specification must be provided."
        logger.error(error_msg)
        raise ValueError(error_msg)

    logger.info("OpenAI: start request")

    try:
        # Load system prompt from file
        system_prompt_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "constants", "data", "system_prompt.txt")
        )
        if not os.path.exists(system_prompt_path):
            raise FileNotFoundError(f"System prompt not found at: {system_prompt_path}")
        
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read().strip()
        
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_request},
            ],
            text={
            "format": {
                "type": "json_schema",
                "name": "FlowditChecklist",
                "schema": schema,
                "strict": True
            }
            }
        )
        logger.info("OpenAI: got response with %d parts", len(resp.output or []))

        # Extract JSON from Responses API result
        try:
            first_block = resp.output[0]
            payload = None
            if getattr(first_block, "type", None) == "output_text":
                payload = first_block.text
            else:
                content = getattr(first_block, "content", None)
                if content and len(content) > 0 and hasattr(content[0], "text"):
                    payload = content[0].text
            if payload is None:
                raise RuntimeError("Unexpected response format from OpenAI Responses API")

            result = json.loads(payload)
            try:
                validate(instance=result, schema=schema)
            except ValidationError as err:
                raise RuntimeError(f"Generated checklist does not match schema: {err.message}") from err
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to parse JSON output: {e}")
    except Exception as e:
        logger.error(f"OpenAI checklist generation failed: {e}", exc_info=True)
        return {"error": str(e), "success": False}
