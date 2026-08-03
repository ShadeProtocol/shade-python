"""
Base model shared by every Shade API response object.
"""
from __future__ import annotations

from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from ..errors import InvalidRequestError


class ShadeObject(BaseModel):
    """Base class for API response models.

    Subclasses get dict round-tripping (:meth:`from_dict` / :meth:`to_dict`) and a
    readable ``repr``. Unknown fields returned by the API are preserved rather than
    rejected, so a server-side addition never breaks an older SDK.

    Attributes:
        _id_field: Name of the field shown in ``repr``. Subclasses that do not use
            a plain ``id`` should override it (e.g. ``_id_field = "invoice_id"``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    _id_field: ClassVar[str] = "id"

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise _as_invalid_request(type(self), exc) from exc

    @classmethod
    def from_dict(cls, data: dict) -> "ShadeObject":
        """Build a model from a decoded JSON object.

        Raises:
            InvalidRequestError: If ``data`` is not a dict or fails validation.
        """
        if not isinstance(data, dict):
            raise InvalidRequestError(
                f"{cls.__name__}.from_dict() expects a dict, got "
                f"{type(data).__name__}"
            )
        return cls(**data)

    def to_dict(self, **kwargs: Any) -> dict:
        """Return the model as a plain dict, keyed by field alias where one exists.

        Any keyword arguments are forwarded to ``model_dump``.
        """
        kwargs.setdefault("by_alias", True)
        return self.model_dump(**kwargs)

    def __repr__(self) -> str:
        identifier = self._identifier()
        if identifier is None:
            return f"<{type(self).__name__}>"
        return f"<{type(self).__name__} {self._id_field}={identifier!r}>"

    def _identifier(self) -> Optional[Any]:
        """Value of the model's primary ID field, or ``None`` when it has none."""
        return getattr(self, self._id_field, None)


def _as_invalid_request(
    model: type[BaseModel],
    exc: ValidationError,
) -> InvalidRequestError:
    """Translate a pydantic ValidationError into the SDK's InvalidRequestError."""
    errors = exc.errors()
    field_errors: dict[str, list[str]] = {}
    for error in errors:
        location = ".".join(str(part) for part in error["loc"]) or "__root__"
        field_errors.setdefault(location, []).append(error["msg"])

    count = len(errors)
    plural = "" if count == 1 else "s"
    first = next(iter(field_errors), None)
    return InvalidRequestError(
        f"{count} validation error{plural} for {model.__name__}",
        param=first,
        field_errors=field_errors,
    )
