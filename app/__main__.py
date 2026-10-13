"""python -m app 으로 키오스크 서버를 띄운다. 주소와 포트는 HOST, PORT 환경 변수로 바꾼다."""
import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("app.server:create_app", factory=True,
                host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
