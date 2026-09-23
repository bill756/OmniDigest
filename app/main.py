import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db.session import init_db
from app.api.v1.deps import cache_client
from app.api.v1.endpoints import router as v1_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动阶段：初始化数据库表结构与多级缓存
    await init_db()
    await cache_client.init()
    yield
    # 关闭阶段：释放资源
    await cache_client.close()


app = FastAPI(
    title=settings.APP_NAME,
    description="跨平台长文深度精读与动态事实核查平台 API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由
app.include_router(v1_router, prefix="/api/v1", tags=["OmniDigest Core"])


# 健康检查
@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "env": settings.APP_ENV,
    }


# 挂载前端交互式页面
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", tags=["Web UI"])
async def serve_index():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": f"欢迎使用 {settings.APP_NAME}！请访问 /docs 查看交互式 API 文档。"}
