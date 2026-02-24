# import uvicorn
# from fastapi import FastAPI, Query
# from typing import List, Optional
#
# app = FastAPI()
#
# @app.get("/api/memes/search")
# def search_memes(
#     q: Optional[str] = None,
#     cursor: Optional[str] = None,
#     limit: int = 50,
#     tags: List[str] = Query(default=[]),
#     language: List[str] = Query(default=[]),
# ):
#     # 1. Decode cursor
#     # 2. Query DB / vector engine
#     # 3. Compute facets
#     # 4. Return results + nextCursor
#     ...
#
# if __name__ == "__main__":
#     uvicorn.run(app,
#                 host="127.0.0.1",
#                 port=8081)
