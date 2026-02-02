from pydantic import BaseModel


class ManualBuyRequest(BaseModel):
    symbol: str
    shares: int | None = None  # If None, auto-calculate from risk
    price: float
    stop_price: float
    target1_price: float
    target2_price: float
    notes: str | None = None


class ManualCloseRequest(BaseModel):
    shares: int | None = None  # If None, close all
    price: float
    reason: str = "manual"


class SettingUpdate(BaseModel):
    key: str
    value: str
