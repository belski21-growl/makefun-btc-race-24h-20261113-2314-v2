"""BTC race bot for make.fun btc-race-24h-20261113-2314 (agent-fun/bars-replay/v1).

Strategy (dip-bounce catcher, long only, full size, few trades):
  * The dev window falls 4.91% ($71,408.76 -> $67,900.67), so the previous
    competition's momentum trend-following entry FAILS here: 0 of 1,152
    momentum configs made money (best $0.00, no trades; the old 55/-20/80
    config loses $26.15). A falling window has no trend to follow.
  * What the window DOES have is a V-bounce: after a ~-5% slide, price
    bottoms near bar 402 (~$67.9k) and rallies +200 bps to ~$69.2k. Buying
    dips blindly loses on average here (after a -100 bps 60-bar drop the
    next-60-min return averages -41 bps: the trend keeps falling), so the
    entry needs BOTH a deep drop AND a bounce confirmation.
  * ENTER long with all cash when the 240-bar return is <= -120 bps (a real
    slide, not noise) AND the 5-bar return is >= +40 bps (the bounce has
    actually started). The 5-bar confirmation is the key filter: with it the
    plateau is wide (drop -100..-140 x confirmation 40-50 bps x take-profit
    120-150 bps all clear the gate at +$9.84..+$13.94); without it
    (confirmation 30 bps) the same rules whipsaw to -$17.
  * EXIT the whole position on take-profit +150 bps from entry, or a
    trailing stop of -100 bps from the peak close since entry, or after
    120 bars held (time exit, rarely binds).
  * After an exit the entry must re-arm (240-bar return below -50 bps
    first), so the bot never re-buys the same fading bounce bar after bar.
  * Orders carry max_slippage_bps=100. No orders on batches that carry no
    new CBBTC bar (gap minutes). No clock, no files, no environment, no
    randomness: two runs on the same data produce identical orders.

Dev-window backtest (kit dataset, exact harness fill math incl. 4 bps fee,
2 bps slippage and constant-product impact vs the bar open depth):
  net PnL +$13.94, 4 trades (2 round trips), max drawdown 0.73%, fees $1.61.
  Round trip 1: buy bar 412 ($68,359.01), sell bar 468 ($69,097.59).
  Round trip 2: buy bar 994 ($68,527.03), sell bar 1114 ($68,849.83).
  Buy-and-hold on the same window loses ~$49 (BTC fell 4.91%); hold-cash
  makes $0.00 and fails the gate (needs PnL > $0 and >= 1 trade).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
DROP_LB = 240         # long lookback for the slide (bars)
DROP_BPS = -120       # 240-bar return needed to enter (bps)
CONF_LB = 5           # short lookback for the bounce confirmation (bars)
CONF_BPS = 40         # 5-bar return needed to enter (bps)
REARM_BPS = -50       # 240-bar return that re-arms entry after an exit (bps)
TP_BPS = 150          # take-profit from entry close (bps)
TRAIL_BPS = 100       # trailing stop from peak close since entry (bps)
MAX_HOLD = 120        # time exit: max bars held
MAX_SLIP_BPS = 100    # max slippage vs the latest close, buy and sell


def bps_return(new, old):
    """Integer basis-point return of `new` vs `old` (floor division)."""
    return (new - old) * 10000 // old


class Bot:
    def __init__(self):
        self.closes = []      # closes seen so far (ints, price scale 5)
        self.last_t = None    # t of the last CBBTC bar processed
        self.entry = None     # entry close (first close seen while holding)
        self.peak = None      # highest close since entry (int)
        self.hold = 0         # bars held in the current position
        self.armed = True     # entry allowed only when the signal has re-armed

    def drop_bps(self, close):
        """240-bar return in bps, or None until enough closes exist."""
        if len(self.closes) < DROP_LB + 1:
            return None
        return bps_return(close, self.closes[-DROP_LB - 1])

    def confirmation_bps(self, close):
        """5-bar return in bps, or None until enough closes exist."""
        if len(self.closes) < CONF_LB + 1:
            return None
        return bps_return(close, self.closes[-CONF_LB - 1])

    def reset_position_state(self):
        """Clear entry tracking when flat (called once per flat batch)."""
        self.entry = None
        self.peak = None
        self.hold = 0

    def exit_reason(self, close):
        """Why to exit now ('tp' / 'trail' / 'time'), or None to keep holding."""
        ret_entry = bps_return(close, self.entry)
        if ret_entry >= TP_BPS:
            return "tp"
        if bps_return(close, self.peak) <= -TRAIL_BPS:
            return "trail"
        if self.hold >= MAX_HOLD:
            return "time"
        return None

    def on_batch(self, state, batch):
        # Find this batch's CBBTC bar, if any. A batch with no new bar (a gap
        # minute) carries no new information and no fill: answer empty.
        bar = None
        for b in batch.get("bars", []):
            if b.get("symbol") == SYMBOL:
                bar = b
                break
        if bar is None or bar.get("t") == self.last_t:
            return []
        self.last_t = bar["t"]
        close = int(bar["c"])
        if close <= 0:
            return []
        self.closes.append(close)
        held = state.positions.get(SYMBOL)
        qty = held.qty if held is not None else 0
        if qty == 0:
            self.reset_position_state()
            drop = self.drop_bps(close)
            if drop is None:
                return []
            # Re-arm: after an exit, the 240-bar return must go below -50 bps.
            if drop < REARM_BPS:
                self.armed = True
            if not self.armed:
                return []
            conf = self.confirmation_bps(close)
            if conf is None:
                return []
            if drop <= DROP_BPS and conf >= CONF_BPS and state.cash >= 1000000:
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash,
                         "max_slippage_bps": MAX_SLIP_BPS}]
            return []
        # Holding: entry is the first close seen while holding (the fill bar).
        if self.entry is None:
            self.entry = close
        self.peak = close if self.peak is None else max(self.peak, close)
        self.hold += 1
        if self.exit_reason(close) is not None:
            self.armed = False
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIP_BPS}]
        return []


BOT = Bot()
makefun_bars.run(BOT.on_batch)
