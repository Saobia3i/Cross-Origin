"""Per-session simulation endpoints (see docs/SIMULATION.md). All require a verified user."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.deps import get_current_verified_user, get_db
from app.models import User
from app.responses import success
from app.schemas import SimulationCreate, SimulationResetRequest
from app.services import simulation_service as sims

router = APIRouter()


@router.post("/api/simulations", status_code=201)
def apply(body: SimulationCreate, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    result = sims.apply_simulation(db, body.session_id, body.scenario_type, body.zone_hub, user.id,
                                   body.origin, body.destination, body.prefer_student_fare)
    return success(result.model_dump(mode="json"), status_code=201)


@router.post("/api/simulations/reset")
def reset(body: SimulationResetRequest, user: User = Depends(get_current_verified_user), db: Session = Depends(get_db)):
    return success(sims.reset_simulation(db, body.session_id, user.id).model_dump(mode="json"))


@router.get("/api/simulations/active")
def active(session_id: str = Query(..., min_length=1, max_length=100), user: User = Depends(get_current_verified_user),
           db: Session = Depends(get_db)):
    sim = sims.get_active_scenario(db, session_id)
    if sim is None:
        return success({"session_id": session_id, "active": False, "scenario": None, "effects": None})
    return success({"session_id": session_id, "active": True, "scenario_id": sim.scenario_id,
                    "scenario_type": sim.scenario_type, "zone_hub": sim.zone_hub, "status": sim.status,
                    "created_at": sim.created_at, "expires_at": sim.expires_at,
                    "effects": sims.get_scenario_effects(db, session_id).model_dump(mode="json")})
