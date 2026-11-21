from fastapi import APIRouter
from app.inputs import Evidence
from . import service

router = APIRouter()


@router.post('/api/runs/{run_id}/corrections')
def register(run_id: str, source: Evidence):
    return {'corrections': service.register(run_id, source.raw())}


@router.get('/api/runs/{run_id}/corrections')
def for_run(run_id: str):
    return service.for_origin(run_id)


@router.get('/api/corrections')
def board():
    return service.board()


@router.get('/api/corrections/{correction_id}')
def detail(correction_id: str):
    return service.detail(correction_id)
