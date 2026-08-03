"""
SwapPayment model for Shade's cross-asset payment routing.

Represents a payment where the payer pays in one token and the
merchant settles in a different token, routed through Shade's
liquidity pools on the Stellar network.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class SwapStatus(str, Enum):
    """Lifecycle states of a swap-routed payment."""

    PENDING = "pending"
    SWAPPING = "swapping"
    COMPLETED = "completed"
    FAILED = "failed"
    SLIPPAGE_EXCEEDED = "slippage_exceeded"


class SwapPayment:
    """Represents a swap-routed payment on the Shade network.

    The payer sends ``pay_in_token`` and the merchant receives
    ``settle_out_token``. Shade routes the conversion through one
    or more liquidity pools, expressed as ``routing_path``.

    Parameters
    ----------
    id : str
        Unique identifier for this swap payment.
    pay_in_token : str
        Asset code (or full Stellar asset string) the payer sends.
    settle_out_token : str
        Asset code the merchant receives after the swap.
    amount_in : Decimal
        Amount of ``pay_in_token`` the payer is sending.
    amount_out : Decimal or None
        Expected / actual amount of ``settle_out_token`` received.
        ``None`` until the swap is executed.
    routing_path : list[str]
        Ordered list of asset codes describing the swap route,
        e.g. ``["USDC", "XLM", "BTC"]``.
    slippage_tolerance : float
        Maximum acceptable price slippage expressed as a fraction in
        the open interval (0, 1).  For example ``0.005`` means 0.5 %.
    status : SwapStatus
        Current lifecycle state of the payment.
    stellar_tx_hash : str or None
        Stellar transaction hash once the swap is submitted on-chain.
    created_at : datetime or None
        UTC timestamp when the swap payment was created.

    Raises
    ------
    ValueError
        If ``slippage_tolerance`` is not in the open interval (0, 1).
    ValueError
        If ``status`` is not a valid :class:`SwapStatus` value.
    """

    def __init__(
        self,
        id: str,
        pay_in_token: str,
        settle_out_token: str,
        amount_in: Decimal,
        routing_path: List[str],
        slippage_tolerance: float,
        status: SwapStatus,
        amount_out: Optional[Decimal] = None,
        stellar_tx_hash: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> None:
        if not (0 < slippage_tolerance < 1):
            raise ValueError(
                f"slippage_tolerance must be in the open interval (0, 1), "
                f"got {slippage_tolerance!r}"
            )

        self.id = id
        self.pay_in_token = pay_in_token
        self.settle_out_token = settle_out_token
        self.amount_in = amount_in
        self.amount_out = amount_out
        self.routing_path: List[str] = list(routing_path)
        self.slippage_tolerance = slippage_tolerance
        # Normalize raw strings to the enum so the invariant always holds.
        self.status: SwapStatus = (
            status if isinstance(status, SwapStatus) else SwapStatus(status)
        )
        self.stellar_tx_hash = stellar_tx_hash
        self.created_at = created_at

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwapPayment":
        """Construct a :class:`SwapPayment` from a raw dictionary.

        This is the canonical way to deserialise an API response or a
        stored record into a ``SwapPayment`` object.

        Parameters
        ----------
        data : dict
            Must contain at minimum: ``id``, ``pay_in_token``,
            ``settle_out_token``, ``amount_in``, ``routing_path``,
            ``slippage_tolerance``, and ``status``.

        Returns
        -------
        SwapPayment
            A fully populated instance.

        Raises
        ------
        ValueError
            If ``slippage_tolerance`` is outside (0, 1).
        KeyError
            If any required field is absent from *data*.
        """
        raw_created_at = data.get("created_at")
        if isinstance(raw_created_at, str):
            created_at: Optional[datetime] = datetime.fromisoformat(raw_created_at)
        elif isinstance(raw_created_at, datetime):
            created_at = raw_created_at
        else:
            created_at = None

        raw_amount_out = data.get("amount_out")
        amount_out: Optional[Decimal] = (
            Decimal(str(raw_amount_out)) if raw_amount_out is not None else None
        )

        return cls(
            id=data["id"],
            pay_in_token=data["pay_in_token"],
            settle_out_token=data["settle_out_token"],
            amount_in=Decimal(str(data["amount_in"])),
            amount_out=amount_out,
            routing_path=list(data["routing_path"]),
            slippage_tolerance=float(data["slippage_tolerance"]),
            status=SwapStatus(data["status"]),
            stellar_tx_hash=data.get("stellar_tx_hash"),
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this instance to a plain dictionary."""
        return {
            "id": self.id,
            "pay_in_token": self.pay_in_token,
            "settle_out_token": self.settle_out_token,
            "amount_in": str(self.amount_in),
            "amount_out": str(self.amount_out) if self.amount_out is not None else None,
            # Return a copy so callers cannot mutate the model's internal list.
            "routing_path": list(self.routing_path),
            "slippage_tolerance": self.slippage_tolerance,
            "status": self.status.value,
            "stellar_tx_hash": self.stellar_tx_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SwapPayment("
            f"id={self.id!r}, "
            f"pay_in_token={self.pay_in_token!r}, "
            f"settle_out_token={self.settle_out_token!r}, "
            f"amount_in={self.amount_in}, "
            f"status={self.status!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SwapPayment):
            return NotImplemented
        return self.id == other.id
