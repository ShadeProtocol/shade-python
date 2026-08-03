"""
Webhook event model.

Represents a parsed, verified webhook event delivered by the Shade platform.
Field names are converted from ``camelCase`` (backend/JSON) to ``snake_case``
(Python) via pydantic field aliases, matching the convention established by
:class:`~shade.models.transfer.Transfer`.

At this layer ``data`` is deliberately left as the raw decoded JSON object. The
resource layer (``Webhook.construct_event()``) is responsible for coercing it
into the corresponding typed model based on ``type``.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, StrictBool

from .base import ShadeObject


class WebhookEventType(str, Enum):
    """Event types delivered by the Shade platform.

    Members subclass :class:`str`, so they compare equal to the wire value::

        if event.type == WebhookEventType.PAYMENT_COMPLETED:
            ...

    The list is not exhaustive by design: :class:`WebhookEvent` stores ``type``
    as a plain ``str``, so an event type added server-side still parses and can
    be compared against a literal until a constant is added here.
    """

    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_CANCELLED = "payment.cancelled"
    PAYMENT_EXPIRED = "payment.expired"
    PAYMENT_PARTIALLY_PAID = "payment.partially_paid"
    INVOICE_PAID = "invoice.paid"
    INVOICE_SENT = "invoice.sent"
    INVOICE_CANCELLED = "invoice.cancelled"
    TRANSFER_COMPLETED = "transfer.completed"
    TRANSFER_FAILED = "transfer.failed"
    SWAP_COMPLETED = "swap.completed"
    SWAP_SLIPPAGE_EXCEEDED = "swap.slippage_exceeded"


class WebhookEvent(ShadeObject):
    """A parsed, verified webhook event.

    The expected payload is a JSON object of the shape::

        {
            "id": "evt_123",
            "type": "payment.completed",
            "data": {"id": "pay_123", ...},
            "createdAt": "2026-07-20T12:00:00Z",
            "livemode": false
        }

    Build one with :meth:`ShadeObject.from_dict`, which maps ``createdAt`` to
    :attr:`created_at` and parses it into a :class:`~datetime.datetime`.
    ``livemode`` distinguishes a production event from a test-mode one.

    ``id``, ``type``, ``data``, ``created_at`` and ``livemode`` are all
    required; a payload missing any of them — or carrying an unparseable
    timestamp — raises
    :class:`~shade.errors.InvalidRequestError` rather than producing a
    half-populated event. Unknown extra keys are preserved, per
    :class:`~shade.models.base.ShadeObject`.

    Attributes:
        id: Unique identifier of the event.
        type: Event type string, e.g. ``"payment.completed"``. Compare against
            :class:`WebhookEventType` members.
        data: The event payload, left as the raw decoded JSON object. Typed
            model coercion happens in the resource layer, not here.
        created_at: When the platform emitted the event.
        livemode: ``True`` for a live event, ``False`` for a test-mode one.
    """

    id: str
    type: str
    data: dict[str, Any]
    created_at: datetime = Field(alias="createdAt")
    livemode: StrictBool
