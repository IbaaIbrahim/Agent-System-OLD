import requests
import json
import uuid

# Configuration
API_BASE_URL = "http://localhost:8000"
# Replace with your actual tenant API key (sk-agent-*)
API_KEY = "sk-agent-your-api-key-here"

def create_web_search_job(query: str):
    """Creates a job with the web_search tool included."""
    
    url = f"{API_BASE_URL}/api/v1/chat/completions"
    
    # Define the web_search tool 
    # Based on services/tool-workers/src/tools/web_search.py
    web_search_tool = {
        "name": "web_search",
        "description": "Search the web for information. Use this when you need to find current information, facts, or data that may not be in your training data.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 10)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    }
    
    payload = {
        "messages": [
            {
                "role": "user",
                "content": query
            }
        ],
        "tools": [web_search_tool],
        "model": "gpt-4o-mini",  # Or your preferred model
        "provider": "openai",     # Or "anthropic"
        "stream": True            # Enable streaming for real-time updates
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    print(f"Sending request to {url}...")
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print("Successfully created job!")
        print(f"Job ID: {data['job_id']}")
        print(f"Stream URL: {data['stream_url']}")
        print(f"Stream Token: {data['stream_token']}")
        return data
    else:
        print(f"Failed to create job. Status code: {response.status_code}")
        print(f"Response: {response.text}")
        return None

if __name__ == "__main__":
    # Example usage
    job = create_web_search_job("What is the current price of Bitcoin?")
    if job:
        print("\nYou can now connect to the Stream URL to see the agent thinking and using the tool.")
