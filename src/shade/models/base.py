"""Base model for Shade API response objects."""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

_T = TypeVar("_T", bound="ShadeObject")


class ShadeObject(BaseModel):
    """Shared base for typed API response models."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the model to a plain dictionary."""
        return self.model_dump()

    @classmethod
    def from_dict(cls: type[_T], data: dict[str, Any]) -> _T:
        """Construct a model instance from an API response dictionary."""
        return cls.model_validate(data)

    def __repr__(self) -> str:
        object_id = getattr(self, "id", None)
        if object_id is not None:
            return f"<{self.__class__.__name__} id={object_id!r}>"
        return f"<{self.__class__.__name__}>"
