"""Lightweight Binance USDⓈ-M Futures REST client.

Implements the endpoints most relevant to automated trading: public market
data, signed account/position queries, order management, and user-data
streams. Authentication uses HMAC-SHA256 over the query string plus the
`X-MBX-APIKEY` header, per Binance's signed-endpoint spec.

Reference: https://developers.binance.com/en/docs/derivatives/usds-margined-futures
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from .exceptions import BinanceAPIError, BinanceRequestError
from .utils import SymbolFilters

DEFAULT_RECV_WINDOW = 5000


class BinanceFuturesClient:
    """Synchronous REST client for Binance USDS-M (USDⓈ-M) Futures."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        recv_window: int = DEFAULT_RECV_WINDOW,
        timeout: float = 10,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout
        self.session = requests.Session()
        self._filters_cache: dict[str, SymbolFilters] = {}

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _sign(self, params: dict) -> dict:
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            params = self._sign(params)

        headers = {"X-MBX-APIKEY": self.api_key} if signed else {}
        url = self.base_url + path
        try:
            resp = self.session.request(method, url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BinanceRequestError(str(exc)) from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response):
        try:
            data = resp.json()
        except ValueError:
            raise BinanceAPIError(-1, f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}", resp.status_code)

        is_error = resp.status_code >= 400
        if isinstance(data, dict) and data.get("code", 0) < 0:
            is_error = True

        if is_error:
            code = data.get("code", resp.status_code) if isinstance(data, dict) else resp.status_code
            msg = data.get("msg", str(data)) if isinstance(data, dict) else str(data)
            raise BinanceAPIError(code, msg, resp.status_code)
        return data

    # ------------------------------------------------------------------
    # Public market data (unsigned)
    # ------------------------------------------------------------------
    def ping(self) -> dict:
        return self._request("GET", "/fapi/v1/ping")

    def server_time(self) -> dict:
        return self._request("GET", "/fapi/v1/time")

    def exchange_info(self) -> dict:
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def depth(self, symbol: str, limit: int = 20) -> dict:
        return self._request("GET", "/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def klines(self, symbol: str, interval: str = "1m", limit: int = 100,
               start_time: int | None = None, end_time: int | None = None) -> list:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._request("GET", "/fapi/v1/klines", params)

    def ticker_price(self, symbol: str | None = None):
        return self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol} if symbol else None)

    def ticker_24hr(self, symbol: str | None = None):
        return self._request("GET", "/fapi/v1/ticker/24hr", {"symbol": symbol} if symbol else None)

    def mark_price(self, symbol: str | None = None):
        return self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol} if symbol else None)

    def funding_rate(self, symbol: str | None = None, limit: int = 100):
        params: dict = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/fapi/v1/fundingRate", params)

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        """Return (cached) precision/lot filters for a symbol."""
        if symbol not in self._filters_cache:
            info = self.exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    self._filters_cache[symbol] = SymbolFilters(s)
                    break
            else:
                raise ValueError(f"Unknown symbol: {symbol}")
        return self._filters_cache[symbol]

    def liquid_symbols(self, limit: int = 40, quote_asset: str = "USDT") -> list[str]:
        """Top `limit` USDT-margined perpetuals by 24h quote volume.

        Used to auto-build a liquid multi-symbol watchlist (avoids illiquid
        pairs with wide spreads / thin testnet order books).
        """
        info = self.exchange_info()
        usdt_perps = {
            s["symbol"] for s in info["symbols"]
            if s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s["symbol"].endswith(quote_asset)
        }
        vol: dict[str, float] = {}
        for t in self.ticker_24hr():
            sym = t.get("symbol")
            if sym in usdt_perps:
                try:
                    vol[sym] = float(t.get("quoteVolume", 0.0))
                except (TypeError, ValueError):
                    vol[sym] = 0.0
        ranked = sorted(vol, key=lambda s: vol[s], reverse=True)
        return ranked[:limit]

    # ------------------------------------------------------------------
    # Account & position (signed)
    # ------------------------------------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def balance(self) -> list:
        return self._request("GET", "/fapi/v2/balance", signed=True)

    def position_risk(self, symbol: str | None = None) -> list:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v2/positionRisk", params, signed=True)

    def income(self, symbol: str | None = None, limit: int = 100, **kwargs):
        params = {"limit": limit, **kwargs}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/fapi/v1/income", params, signed=True)

    def change_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True)

    def change_margin_type(self, symbol: str, margin_type: str) -> dict:
        return self._request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, signed=True)

    def set_position_mode(self, dual_side_position: bool) -> dict:
        return self._request(
            "POST", "/fapi/v1/positionSide/dual",
            {"dualSidePosition": "true" if dual_side_position else "false"},
            signed=True,
        )

    # ------------------------------------------------------------------
    # Orders (signed)
    # ------------------------------------------------------------------
    def place_order(self, symbol: str, side: str, type: str, quantity=None, **kwargs) -> dict:
        """Place an order. `side` ∈ {BUY, SELL}; `type` ∈ {LIMIT, MARKET, STOP,
        STOP_MARKET, TAKE_PROFIT, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET}.

        Extra kwargs map 1:1 to Binance order params: price, timeInForce,
        reduceOnly, newClientOrderId, stopPrice, closePosition, activationPrice,
        callbackRate, workingType, priceProtect, positionSide, priceMatch,
        selfTradePreventionMode, goodTillDate, ...
        """
        params = {"symbol": symbol, "side": side, "type": type}
        if quantity is not None:
            params["quantity"] = quantity
        params.update(kwargs)
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def batch_orders(self, orders: list[dict]) -> list:
        """Place multiple orders in one request. Each dict is a full order payload."""
        return self._request("POST", "/fapi/v1/batchOrders", {"batchOrders": self._to_json(orders)}, signed=True)

    def get_order(self, symbol: str, order_id: int | None = None,
                  orig_client_order_id: str | None = None) -> dict:
        params = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        return self._request("GET", "/fapi/v1/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int | None = None,
                     orig_client_order_id: str | None = None) -> dict:
        params = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        return self._request("DELETE", "/fapi/v1/order", params, signed=True)

    def cancel_all_open_orders(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def place_algo_order(self, symbol: str, side: str, type: str, trigger_price,
                         close_position: bool = True, working_type: str = "MARK_PRICE", **kwargs) -> dict:
        """Place a conditional TP/SL order via the Algo Order endpoint.

        Binance moved STOP_MARKET / TAKE_PROFIT_MARKET off `/fapi/v1/order`; they
        now go to POST /fapi/v1/algoOrder with `algoType=CONDITIONAL` and the
        trigger price passed as `triggerPrice` (not `stopPrice`).
        """
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": type,
            "closePosition": "true" if close_position else "false",
            "triggerPrice": trigger_price,
            "workingType": working_type,
        }
        params.update(kwargs)
        return self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    def cancel_all_algo_orders(self, symbol: str) -> dict:
        """Cancel all open algo (TP/SL) orders for a symbol."""
        return self._request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, signed=True)

    def algo_open_orders(self, symbol: str | None = None) -> list:
        """Query current open algo (TP/SL) orders."""
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v1/openAlgoOrders", params, signed=True)

    def open_orders(self, symbol: str | None = None) -> list:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v1/openOrders", params, signed=True)

    def all_orders(self, symbol: str, **kwargs) -> list:
        return self._request("GET", "/fapi/v1/allOrders", {"symbol": symbol, **kwargs}, signed=True)

    # ------------------------------------------------------------------
    # User data stream (listen key)
    # ------------------------------------------------------------------
    def start_user_data_stream(self) -> dict:
        return self._request("POST", "/fapi/v1/listenKey", signed=True)

    def keepalive_user_data_stream(self, listen_key: str) -> dict:
        return self._request("PUT", "/fapi/v1/listenKey", {"listenKey": listen_key}, signed=True)

    def close_user_data_stream(self, listen_key: str) -> dict:
        return self._request("DELETE", "/fapi/v1/listenKey", {"listenKey": listen_key}, signed=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_json(orders: list[dict]) -> str:
        import json
        return json.dumps(orders)
