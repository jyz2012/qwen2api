from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sth import *
import asyncio
import time
import hashlib
from typing import Dict, Optional


token_cache: Dict[str, dict] = {}
cache_lock = asyncio.Lock()


def get_cache_key(email: str) -> str:
    return hashlib.md5(email.encode()).hexdigest()


async def get_cached_token(email: str) -> Optional[str]:
    key = get_cache_key(email)
    async with cache_lock:
        if key in token_cache:
            cached = token_cache[key]
            if time.time() < cached["expires_at"]:
                return cached["token"]
            else:
                del token_cache[key]
    return None


async def cache_token(email: str, token: str, expires_at: int):
    key = get_cache_key(email)
    async with cache_lock:
        token_cache[key] = {"token": token, "expires_at": expires_at, "email": email}


app = FastAPI()


@app.get("/")
def index():
    return {"Hello": "World"}


@app.get("/v1/models")
def get_models():
    return {"Hello": "World"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    # print('Body: ',body)
    model = body.get("model")
    messages = body.get("messages")
    stream = body.get("stream")
    # print(messages)

    # print('Headers: ',request.headers)
    auth = request.headers.get("Authorization", "")
    # Key format: 'email:password'
    email, password = parse_api_key(auth)

    # 使用缓存的 token
    token = await get_cached_token(email)
    if not token:
        token, expires_at = await login_with_password(email, password)
        await cache_token(email, token, expires_at)
        # print(f"Cached token for {email} expires at {expires_at}")

    # To authorize
    code_verifier, code_challenge = generate_pkce_pair()
    device_code_info = request_device_code(code_challenge)
    # print(f"To {device_code_info['verification_uri']}?user_code={device_code_info['user_code']}&client=qwen-code")
    authorize(device_code_info["user_code"], token)
    # print('Authorized.')

    # Get the token
    token_response = poll_for_token(device_code_info["device_code"], code_verifier)
    access_token = token_response["access_token"]
    # refresh_token = token_response['refresh_token']

    result = call_qwen_api(access_token, model, messages, stream)

    if stream:

        def iter_chunks():
            try:
                for chunk in result:
                    yield f"data: {chunk.to_json(indent=None)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                try:
                    result.close()
                except Exception:
                    pass

        return StreamingResponse(iter_chunks(), media_type="text/event-stream")
    else:
        return JSONResponse(content=result.to_dict(mode="json"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
