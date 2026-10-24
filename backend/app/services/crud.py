from sqlalchemy.orm import Session
from typing import Type, TypeVar, List, Optional
from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")


class CRUDBase:
    """Base class for CRUD operations with user isolation."""

    def __init__(self, model: Type[ModelType]):
        self.model = model

    def get(self, db: Session, id: int, user_id: int) -> Optional[ModelType]:
        """Get a single record by id for a specific user."""
        return (
            db.query(self.model)
            .filter(self.model.id == id, self.model.user_id == user_id)
            .first()
        )

    def get_multi(
        self, db: Session, user_id: int, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        """Get multiple records for a specific user."""
        return (
            db.query(self.model)
            .filter(self.model.user_id == user_id)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def create(self, db: Session, *, obj_in: CreateSchemaType, user_id: int) -> ModelType:
        """Create a new record for a specific user."""
        obj_in_data = obj_in.model_dump() if hasattr(obj_in, "model_dump") else obj_in.dict()
        obj_in_data["user_id"] = user_id
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: UpdateSchemaType,
        user_id: int,
    ) -> ModelType:
        """Update a record for a specific user."""
        update_data = obj_in.model_dump() if hasattr(obj_in, "model_dump") else obj_in.dict()
        # Filter updates by user_id to prevent cross-user updates
        for field in update_data:
            setattr(db_obj, field, update_data[field])
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, id: int, user_id: int) -> Optional[ModelType]:
        """Delete a record for a specific user."""
        obj = (
            db.query(self.model)
            .filter(self.model.id == id, self.model.user_id == user_id)
            .first()
        )
        if obj:
            db.delete(obj)
            db.commit()
        return obj