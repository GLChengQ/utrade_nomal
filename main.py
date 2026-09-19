"""Command-line interface for the Binance USDS-M Futures testnet bot.

Usage examples:
    python main.py status
    python main.py price BTCUSDT
    python main.py depth BTCUSDT --limit 5
    python main.py klines BTCUSDT --interval 15m --limit 10
    python main.py position BTCUSDT
    python main.py leverage BTCUSDT 5
    python main.py order --symbol BTCUSDT --side BUY --type MARKET --qty 0.001
    python main.py order --symbol BTCUSDT --side BUY --type LIMIT --qty 0.001 --price 50000 --round
    python main.py cancel BTCUSDT --order-id 123456
    python main.py cancel-all BTCUSDT
    python main.py orders BTCUSDT
    python main.py listen-key
"""
from __future__ import annotations

import argparse
import json
import sys

import config
from binance_futures import BinanceAPIError, BinanceFuturesClient, BinanceRequestError

ORDER_TYPES = [
    "LIMIT", "MARKET", "STOP", "STOP_MARKET",
    "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET",
]
TIME_IN_FORCE = ["GTC", "IOC", "FOK", "GTX"]


def build_client() -> BinanceFuturesClient:
    if not config.API_KEY or not config.API_SECRET:
        sys.exit("Missing credentials. Set BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET in `.env`.")
    return BinanceFuturesClient(
        config.API_KEY,
        config.API_SECRET,
        config.REST_BASE_URL,
        recv_window=config.RECV_WINDOW,
        timeout=config.REQUEST_TIMEOUT,
    )


def print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ----------------------------------------------------------------------
# Command handlers
# ----------------------------------------------------------------------
def cmd_status(client: BinanceFuturesClient, _args) -> None:
    env = "TESTNET" if config.TESTNET else "MAINNET"
    print(f"Environment : {env}")
    print(f"REST base   : {config.REST_BASE_URL}")
    client.ping()
    print("Ping        : OK")
    server_time = client.server_time()
    print(f"Server time : {server_time['serverTime']} ms")

    account = client.account()
    print(f"Wallet      : total={account.get('totalWalletBalance')}  available={account.get('availableBalance')}  "
          f"unrealizedPnL={account.get('totalUnrealizedProfit')}")

    print("Balances (> 0):")
    for b in client.balance():
        if float(b.get("balance", 0)) > 0:
            print(f"  {b['asset']:<6} balance={b['balance']:<12} available={b.get('availableBalance')}")

    positions = [p for p in client.position_risk() if float(p.get("positionAmt", 0)) != 0]
    print(f"Open positions: {len(positions)}")
    for p in positions:
        print(f"  {p['symbol']}  side={p.get('positionSide')}  amt={p.get('positionAmt')}  "
              f"entry={p.get('entryPrice')}  mark={p.get('markPrice')}  "
              f"uPnL={p.get('unRealizedProfit')}  liq={p.get('liquidationPrice')}")


def cmd_price(client: BinanceFuturesClient, args) -> None:
    print_json(client.ticker_price(args.symbol))


def cmd_depth(client: BinanceFuturesClient, args) -> None:
    print_json(client.depth(args.symbol, args.limit))


def cmd_klines(client: BinanceFuturesClient, args) -> None:
    rows = client.klines(args.symbol, args.interval, args.limit)
    print(f"{'open time':<16}{'open':>14}{'high':>14}{'low':>14}{'close':>14}{'volume':>14}")
    for k in rows:
        print(f"{k[0]:<16}{k[1]:>14}{k[2]:>14}{k[3]:>14}{k[4]:>14}{k[5]:>14}")


def cmd_mark_price(client: BinanceFuturesClient, args) -> None:
    print_json(client.mark_price(args.symbol))


def cmd_funding(client: BinanceFuturesClient, args) -> None:
    print_json(client.funding_rate(args.symbol, args.limit))


def cmd_position(client: BinanceFuturesClient, args) -> None:
    print_json(client.position_risk(args.symbol))


def cmd_leverage(client: BinanceFuturesClient, args) -> None:
    print_json(client.change_leverage(args.symbol, args.leverage))


def cmd_margin_type(client: BinanceFuturesClient, args) -> None:
    print_json(client.change_margin_type(args.symbol, args.margin_type))


def cmd_order(client: BinanceFuturesClient, args) -> None:
    params = {"symbol": args.symbol, "side": args.side, "type": args.type}

    qty = args.qty
    price = args.price
    if args.round and qty is not None:
        filters = client.symbol_filters(args.symbol)
        qty = filters.format_qty(qty)
        if price is not None and args.type != "MARKET":
            price = filters.format_price(price)

    if qty is not None:
        params["quantity"] = qty
    if price is not None:
        params["price"] = price
    if args.tif:
        params["timeInForce"] = args.tif
    if args.stop_price:
        params["stopPrice"] = args.stop_price
    if args.activation_price:
        params["activationPrice"] = args.activation_price
    if args.callback_rate:
        params["callbackRate"] = args.callback_rate
    if args.position_side:
        params["positionSide"] = args.position_side
    if args.working_type:
        params["workingType"] = args.working_type
    if args.client_id:
        params["newClientOrderId"] = args.client_id
    if args.reduce_only:
        params["reduceOnly"] = "true"
    if args.close_position:
        params["closePosition"] = "true"

    result = client.place_order(**params)
    print_json(result)


def cmd_cancel(client: BinanceFuturesClient, args) -> None:
    result = client.cancel_order(
        args.symbol,
        order_id=args.order_id,
        orig_client_order_id=args.client_id,
    )
    print_json(result)


def cmd_cancel_all(client: BinanceFuturesClient, args) -> None:
    print_json(client.cancel_all_open_orders(args.symbol))


def cmd_orders(client: BinanceFuturesClient, args) -> None:
    print_json(client.open_orders(args.symbol))


def cmd_order_history(client: BinanceFuturesClient, args) -> None:
    print_json(client.all_orders(args.symbol, limit=args.limit))


def cmd_listen_key(client: BinanceFuturesClient, args) -> None:
    if args.close:
        print_json(client.close_user_data_stream(args.listen_key))
    elif args.keepalive:
        print_json(client.keepalive_user_data_stream(args.listen_key))
    else:
        print_json(client.start_user_data_stream())


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="futures", description="Binance USDS-M Futures testnet CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Connectivity + account + positions summary")

    p = sub.add_parser("price", help="Latest price")
    p.add_argument("symbol", nargs="?", default=None)

    p = sub.add_parser("depth", help="Order book")
    p.add_argument("symbol")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("klines", help="Candlesticks")
    p.add_argument("symbol")
    p.add_argument("--interval", default="1m")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("mark", help="Mark price / funding info")
    p.add_argument("symbol", nargs="?", default=None)

    p = sub.add_parser("funding", help="Funding rate history")
    p.add_argument("symbol", nargs="?", default=None)
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("position", help="Position risk for a symbol (or all)")
    p.add_argument("symbol", nargs="?", default=None)

    p = sub.add_parser("leverage", help="Change leverage for a symbol")
    p.add_argument("symbol")
    p.add_argument("leverage", type=int)

    p = sub.add_parser("margin-type", help="Change margin type (ISOLATED / CROSSED)")
    p.add_argument("symbol")
    p.add_argument("margin_type", choices=["ISOLATED", "CROSSED"])

    p = sub.add_parser("order", help="Place an order")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["BUY", "SELL"])
    p.add_argument("--type", required=True, choices=ORDER_TYPES)
    p.add_argument("--qty")
    p.add_argument("--price")
    p.add_argument("--stop-price")
    p.add_argument("--activation-price")
    p.add_argument("--callback-rate")
    p.add_argument("--tif", choices=TIME_IN_FORCE)
    p.add_argument("--position-side", choices=["BOTH", "LONG", "SHORT"])
    p.add_argument("--working-type", choices=["MARK_PRICE", "CONTRACT_PRICE"])
    p.add_argument("--client-id")
    p.add_argument("--reduce-only", action="store_true")
    p.add_argument("--close-position", action="store_true")
    p.add_argument("--round", action="store_true", help="Round qty/price to exchange precision")

    p = sub.add_parser("cancel", help="Cancel a single order")
    p.add_argument("symbol")
    p.add_argument("--order-id", type=int)
    p.add_argument("--client-id")

    p = sub.add_parser("cancel-all", help="Cancel all open orders for a symbol")
    p.add_argument("symbol")

    p = sub.add_parser("orders", help="List open orders")
    p.add_argument("symbol", nargs="?", default=None)

    p = sub.add_parser("history", help="Order history for a symbol")
    p.add_argument("symbol")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("listen-key", help="User data stream listen key")
    p.add_argument("--close", help="Close this listen key")
    p.add_argument("--keepalive", help="Keepalive this listen key")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "status": cmd_status,
        "price": cmd_price,
        "depth": cmd_depth,
        "klines": cmd_klines,
        "mark": cmd_mark_price,
        "funding": cmd_funding,
        "position": cmd_position,
        "leverage": cmd_leverage,
        "margin-type": cmd_margin_type,
        "order": cmd_order,
        "cancel": cmd_cancel,
        "cancel-all": cmd_cancel_all,
        "orders": cmd_orders,
        "history": cmd_order_history,
        "listen-key": cmd_listen_key,
    }

    client = build_client()
    try:
        handlers[args.command](client, args)
    except (BinanceAPIError, BinanceRequestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
