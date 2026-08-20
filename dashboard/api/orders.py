"""Order control endpoints (write API)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    notional: Optional[float] = None
    qty: Optional[float] = None
    source: str = "dashboard"


class CloseAllRequest(BaseModel):
    source: str = "dashboard"


@router.post("/api/order/buy")
async def order_buy(req: OrderRequest):
    """Place a buy order."""
    try:
        from execution.order_router import place_order
        result, trade_id = place_order(
            symbol=req.symbol, side="buy", notional=req.notional,
            source=req.source, metadata={"dashboard": True}
        )
        return {
            "success": result.success,
            "trade_id": trade_id,
            "message": result.telegram_message() if hasattr(result, 'telegram_message') else str(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/order/sell")
async def order_sell(req: OrderRequest):
    """Place a sell order."""
    try:
        from execution.order_router import place_order
        result, trade_id = place_order(
            symbol=req.symbol, side="sell", qty=req.qty if req.qty else None,
            notional=req.notional, source=req.source,
            metadata={"dashboard": True}
        )
        return {
            "success": result.success,
            "trade_id": trade_id,
            "message": result.telegram_message() if hasattr(result, 'telegram_message') else str(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/order/close-all")
async def order_close_all(req: CloseAllRequest):
    """Close all open positions at live prices."""
    try:
        from execution.order_router import place_order
        result, trade_id = place_order(
            symbol="CLOSE_ALL", side="sell", source=req.source
        )
        return {
            "success": result.success,
            "trade_id": trade_id,
            "message": str(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/order/close-one")
async def order_close_one(req: OrderRequest):
    """Close a specific position."""
    try:
        from execution.order_router import place_order
        result, trade_id = place_order(
            symbol=req.symbol, side="sell", source=req.source
        )
        return {
            "success": result.success,
            "trade_id": trade_id,
            "message": str(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/order/cancel")
async def order_cancel(order_id: str = Query(...)):
    """Cancel a pending order."""
    try:
        from execution.order_router import place_order
        result, trade_id = place_order(
            symbol="CANCEL", source="dashboard", metadata={"order_id": order_id}
        )
        return {"success": result.success, "message": str(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
