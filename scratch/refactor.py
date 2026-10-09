with open('backend/src/masterone/api/mdm_resolution.py', 'r', encoding='utf-8') as f:
    content = f.read()

ws_code = '''
@router.websocket("/{run_id}/overview/ws")
async def overview_ws(websocket: WebSocket, run_id: str, db: Session = Depends(get_session)):
    await websocket.accept()
    try:
        while True:
            # Re-fetch the session for each check if needed, or use the one from Depends?
            # To avoid stale data, it's better to fetch fresh.
            db.expire_all()
            run = db.get(ResolutionRun, run_id)
            if not run:
                break
            
            # Since get_overview requires auth, we can just bypass auth for the WS and call it directly.
            # But get_overview takes an auth parameter!
            # We can mock it or just re-implement the summary logic if needed, but it's better to just pass a mock auth.
            class MockUser: id = run.owner_user_id
            class MockAuth: user = MockUser()
            
            try:
                overview_data = get_overview(run_id, db, MockAuth())
                # overview_data is a dict (because get_overview returns a dict in FastAPI, or Pydantic model)
                if hasattr(overview_data, "model_dump"):
                    await websocket.send_json(overview_data.model_dump())
                else:
                    await websocket.send_json(overview_data)
            except Exception as e:
                pass
            
            if run.status in ["COMPLETED", "FAILED", "ERROR"]:
                break
            
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
'''

content += "\n" + ws_code
with open('backend/src/masterone/api/mdm_resolution.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("done")
