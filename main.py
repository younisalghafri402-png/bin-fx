        bot_running = True
        live["status"] = "running"
        bot_task = asyncio.create_task(run_bot())
        await broadcast({"type": "status_change", "status": "running"})
    return {"ok": True}

@app.post("/bot/stop")
async def stop():
    global bot_running, bot_task
    bot_running = False
    if bot_task:
        bot_task.cancel()
        bot_task = None
    live["status"] = "stopped"
    await broadcast({"type": "status_change", "status": "stopped"})
    return {"ok": True}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        await ws.send_json({"type": "tick", **live})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in clients:
            clients.remove(ws)

if __name__ == "__main__":
    print("=" * 50)
    print("XAUUSDT Scalping Bot")
    print(f"Dashboard: http://localhost:{PORT}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT)