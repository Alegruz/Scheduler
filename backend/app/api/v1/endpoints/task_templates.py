import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import TaskTemplate
from ....db.session import get_db
from ....schemas.schemas import TaskTemplateCreate, TaskTemplateResponse, TaskTemplateUpdate

router = APIRouter(prefix="/task-templates", tags=["task-templates"])


@router.post("", response_model=TaskTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    body: TaskTemplateCreate,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    template = TaskTemplate(user_id=user_id, **body.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("", response_model=List[TaskTemplateResponse])
def list_templates(
    is_active: Optional[bool] = Query(default=None),
    category: Optional[str] = Query(default=None),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    q = db.query(TaskTemplate).filter(TaskTemplate.user_id == user_id)
    if is_active is not None:
        q = q.filter(TaskTemplate.is_active == is_active)
    if category:
        q = q.filter(TaskTemplate.category == category)
    return q.order_by(TaskTemplate.priority.desc(), TaskTemplate.name).all()


@router.get("/{template_id}", response_model=TaskTemplateResponse)
def get_template(
    template_id: uuid.UUID,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    t = db.query(TaskTemplate).filter(
        TaskTemplate.id == template_id, TaskTemplate.user_id == user_id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task template not found")
    return t


@router.patch("/{template_id}", response_model=TaskTemplateResponse)
def update_template(
    template_id: uuid.UUID,
    body: TaskTemplateUpdate,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    t = db.query(TaskTemplate).filter(
        TaskTemplate.id == template_id, TaskTemplate.user_id == user_id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task template not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: uuid.UUID,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    t = db.query(TaskTemplate).filter(
        TaskTemplate.id == template_id, TaskTemplate.user_id == user_id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task template not found")
    db.delete(t)
    db.commit()
