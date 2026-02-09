import asyncio
import openai
import os

async def main():
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # None uses env var OPENAI_API_KEY
    try:
        resp = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{"role":"user","content":"Say hi"}],
            stream=False,
        )
        print("OK:", getattr(resp, "choices", None) is not None)
    except Exception as e:
        print("ERR:", type(e).__name__, str(e)[:1000])

if __name__ == "__main__":
    asyncio.run(main())