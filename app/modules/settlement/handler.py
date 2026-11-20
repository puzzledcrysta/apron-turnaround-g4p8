import json
from fastapi import APIRouter, Response
from app.inputs import Paired, SettlementCreate, AdjustmentDecision
from . import service

router = APIRouter()


@router.post('/api/settlements')
def create(payload: SettlementCreate):
    return service.create(payload.base_run_id)


@router.get('/api/settlements')
def list_settlements():
    return service.list_all()


@router.get('/api/settlements/{settlement_id}')
def detail(settlement_id: str):
    return service.detail(settlement_id)


@router.post('/api/settlements/{settlement_id}/adjustments')
def register(settlement_id: str, payload: Paired):
    return service.register(settlement_id, payload.raw(), payload.ledger())


@router.post('/api/settlements/{settlement_id}/adjustments/{adjustment_id}/decision')
def decide(settlement_id: str, adjustment_id: int, payload: AdjustmentDecision):
    return service.decide(settlement_id, adjustment_id, payload.decision, payload.note)


@router.post('/api/settlements/{settlement_id}/versions')
def produce(settlement_id: str):
    return service.produce(settlement_id)


@router.get('/api/settlements/{settlement_id}/versions/{version_no}')
def version(settlement_id: str, version_no: int):
    return service.version(settlement_id, version_no)


@router.post('/api/settlements/{settlement_id}/close')
def close(settlement_id: str):
    return service.close(settlement_id)


@router.get('/api/settlements/{settlement_id}/export')
def export(settlement_id: str):
    bundle = service.export_bundle(settlement_id)
    return Response(json.dumps(bundle, ensure_ascii=False, indent=2),
                    media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="settlement-{settlement_id}.json"'})
