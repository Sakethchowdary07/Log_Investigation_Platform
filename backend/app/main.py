"""FastAPI application wiring parsing, detection, and persistence together."""

from __future__ import annotations

from contextlib import asynccontextmanager

from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .correlation import correlate, parse_deployments
from .detection import SpikeDetector
from .knowledge_base import KnowledgeBase, RuleBasedAnalyzer
from .models import AnalysisResult
from .parsers import parse_logs
from .storage import Storage

FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class AppState:
    """Holds runtime singletons so tests can rebuild them with temp paths."""

    def __init__(self) -> None:
        self.storage: Storage | None = None
        self.knowledge_base: KnowledgeBase | None = None

    def init(self) -> None:
        self.storage = Storage()
        self.storage.init_db()
        self.knowledge_base = KnowledgeBase.load()


def create_app() -> FastAPI:
    state = AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.init()
        yield

    app = FastAPI(title="LogSleuth", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.logsleuth = state

    def get_state() -> AppState:
        if state.storage is None or state.knowledge_base is None:
            state.init()
        return state

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/knowledge-base")
    def knowledge_base(st: AppState = Depends(get_state)) -> dict:
        return {"rules": st.knowledge_base.as_list()}

    @app.post("/api/upload", response_model=AnalysisResult)
    async def upload(
        file: UploadFile = File(...),
        deployments: Optional[UploadFile] = File(None),
        st: AppState = Depends(get_state),
    ) -> AnalysisResult:
        raw = await file.read()
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")

        entries = parse_logs(text)
        detector = SpikeDetector(analyzer=RuleBasedAnalyzer(st.knowledge_base))
        result = detector.detect(entries, filename=file.filename or "upload.log")

        # Optional deployment-timeline CSV: correlate spikes to nearby deploys.
        if deployments is not None:
            deploy_raw = await deployments.read()
            if deploy_raw and deploy_raw.strip():
                deploy_text = deploy_raw.decode("utf-8", errors="replace")
                deploy_events = parse_deployments(deploy_text)
                result.deployment_correlations = correlate(
                    result.detections, deploy_events
                )

        analysis_id = st.storage.save_analysis(result)
        result.analysis_id = analysis_id
        return result

    @app.get("/api/analyses", response_model=list[AnalysisResult])
    def list_analyses(st: AppState = Depends(get_state)) -> list[AnalysisResult]:
        return st.storage.list_analyses()

    @app.get("/api/analyses/{analysis_id}", response_model=AnalysisResult)
    def get_analysis(analysis_id: int, st: AppState = Depends(get_state)) -> AnalysisResult:
        result = st.storage.get_analysis(analysis_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return result

    return app


app = create_app()
