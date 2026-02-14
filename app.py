from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sth import *


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
    model=body.get("model")
    messages=body.get("messages")
    stream=body.get('stream')
    # print(messages)

    # print('Headers: ',request.headers)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else auth

    code_verifier, code_challenge = generate_pkce_pair()
    device_code_info = request_device_code(code_challenge)
    # print(f"To {device_code_info['verification_uri']}?user_code={device_code_info['user_code']}&client=qwen-code")
    authorize(device_code_info['user_code'],token)
    # print('Authorized.')
    
    token_response = poll_for_token(device_code_info['device_code'], code_verifier)
    access_token = token_response['access_token']
    # refresh_token = token_response['refresh_token']
        
    result = call_qwen_api(access_token,model,messages,stream)

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
