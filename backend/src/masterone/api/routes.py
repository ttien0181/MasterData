from fastapi import APIRouter, HTTPException

from masterone.config import get_settings
from masterone.models.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat với AI agent."""
    if not get_settings().sample_chat_enabled:
        raise HTTPException(status_code=404, detail='Sample chat is disabled')
    from masterone.agents.graph import agent
    try:
        result = await agent.ainvoke({"query": request.message})
        return ChatResponse(
            response=result.get("response", ""),
            analysis=result.get("analysis", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "MasterOne MVP"}
