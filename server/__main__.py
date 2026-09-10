import os
import uvicorn

uvicorn.run("server.main:app", host="0.0.0.0", port=int(os.getenv("AGENTWATCH_PORT", "8765")),
            workers=1, access_log=False, timeout_graceful_shutdown=3)
