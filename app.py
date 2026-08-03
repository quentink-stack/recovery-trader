"""Local desktop screener for research on post-earnings recovery setups."""

from __future__ import annotations

import csv
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from alpaca import AlpacaMarketData
from backtest import BacktestResult, STRATEGIES, run_dip_recovery_proxy, run_strategy
from scoring import Candidate, ScreenConfig, rebound_score, screen
from screener import DropResearch, latest_large_drop, load_watchlist

ROOT = Path(__file__).parent
SAMPLE = ROOT / "data" / "sample_candidates.csv"
WATCHLIST = ROOT / "data" / "watchlist.csv"
REQUIRED_COLUMNS = {
    "ticker", "company", "drop_pct", "low_hold_pct", "iv_percentile",
    "call_open_interest", "dte", "risk_note",
}


def load_candidates(path: Path) -> list[Candidate]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ValueError("CSV must contain: " + ", ".join(sorted(REQUIRED_COLUMNS)))
        items = []
        for line, row in enumerate(reader, start=2):
            try:
                items.append(Candidate(
                    ticker=row["ticker"].upper().strip(), company=row["company"].strip(),
                    drop_pct=float(row["drop_pct"]), low_hold_pct=float(row["low_hold_pct"]),
                    iv_percentile=float(row["iv_percentile"]),
                    call_open_interest=int(float(row["call_open_interest"])), dte=int(float(row["dte"])),
                    risk_note=row["risk_note"].strip(),
                ))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid value on CSV line {line}: {exc}") from exc
    return items


class RecoveryTrader(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.candidates = load_candidates(SAMPLE)
        self.source_label = tk.StringVar(value=f"Loaded sample data: {SAMPLE.name}")
        self.status = tk.StringVar(value="Ready")
        self.min_drop = tk.StringVar(value="5")
        self.max_iv = tk.StringVar(value="75")
        self.min_oi = tk.StringVar(value="500")
        self.min_dte = tk.StringVar(value="30")
        self.max_dte = tk.StringVar(value="45")
        self.alpaca: AlpacaMarketData | None = None
        self._build()
        self.apply_screen()

    def _build(self) -> None:
        self.master.title("Recovery Trader — earnings dip screener")
        self.master.minsize(920, 580)
        self.pack(fill="both", expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, text="Post-earnings recovery screener", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text="Research queue for sharp earnings-day drops; 30–45 DTE by default.").grid(row=1, column=0, sticky="w", pady=(2, 12))

        filters = ttk.LabelFrame(self, text="Screen filters", padding=10)
        filters.grid(row=2, column=0, sticky="ew")
        for col in range(6): filters.columnconfigure(col, weight=1)
        fields = [("Minimum drop (%)", self.min_drop), ("Maximum IV percentile", self.max_iv), ("Minimum call OI", self.min_oi), ("Minimum DTE", self.min_dte), ("Maximum DTE", self.max_dte)]
        for col, (label, variable) in enumerate(fields):
            ttk.Label(filters, text=label).grid(row=0, column=col, sticky="w", padx=(0, 8))
            ttk.Entry(filters, textvariable=variable, width=12).grid(row=1, column=col, sticky="ew", padx=(0, 8), pady=(2, 0))
        ttk.Button(filters, text="Apply", command=self.apply_screen).grid(row=1, column=5, sticky="ew", pady=(2, 0))

        table_frame = ttk.Frame(self)
        table_frame.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        table_frame.columnconfigure(0, weight=1); table_frame.rowconfigure(0, weight=1)
        columns = ("ticker", "company", "drop", "hold", "iv", "oi", "dte", "score", "risk")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {"ticker":"Ticker", "company":"Company", "drop":"Dip", "hold":"Low hold", "iv":"IV pct.", "oi":"Call OI", "dte":"DTE", "score":"Score", "risk":"Key risk"}
        widths = {"ticker":70,"company":145,"drop":65,"hold":75,"iv":65,"oi":85,"dte":55,"score":65,"risk":245}
        for column in columns:
            self.table.heading(column, text=headings[column]); self.table.column(column, width=widths[column], anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns"); self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self.show_detail)

        lower = ttk.Frame(self)
        lower.grid(row=4, column=0, sticky="ew", pady=(12, 0)); lower.columnconfigure(1, weight=1)
        ttk.Button(lower, text="Load CSV…", command=self.import_csv).grid(row=0, column=0, sticky="w")
        ttk.Label(lower, textvariable=self.source_label).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Button(lower, text="Connect Alpaca Basic", command=self.connect_alpaca).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(lower, text="Run dip-recovery backtest", command=self.run_backtest).grid(row=2, column=2, sticky="e", pady=(8, 0))
        ttk.Button(lower, text="Screen watchlist drops", command=self.screen_watchlist).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(lower, text="Compare strategies", command=self.compare_strategies).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Button(lower, text="Export screened CSV…", command=self.export_csv).grid(row=0, column=2, sticky="e")
        self.detail = ttk.Label(self, textvariable=self.status, wraplength=850, justify="left")
        self.detail.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(self, text="Educational research tool only. Verify earnings drivers, price data, and risk before trading.", style="Muted.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 0))

    def config(self) -> ScreenConfig:
        try:
            config = ScreenConfig(float(self.min_drop.get()), float(self.max_iv.get()), int(self.min_oi.get()), int(self.min_dte.get()), int(self.max_dte.get()))
        except ValueError as exc:
            raise ValueError("All filters must be numeric.") from exc
        if config.min_dte > config.max_dte: raise ValueError("Minimum DTE cannot exceed maximum DTE.")
        return config

    def apply_screen(self) -> None:
        try:
            self.results = screen(self.candidates, self.config())
        except ValueError as exc:
            messagebox.showerror("Invalid filters", str(exc)); return
        self.table.delete(*self.table.get_children())
        for item in self.results:
            self.table.insert("", "end", iid=item.ticker, values=(item.ticker, item.company, f"−{item.drop_pct:.1f}%", f"{item.low_hold_pct:.0f}%", f"{item.iv_percentile:.0f}", f"{item.call_open_interest:,}", item.dte, f"{rebound_score(item)}/100", item.risk_note))
        self.status.set(f"{len(self.results)} candidate(s) passed. Select a row for its research checklist.")

    def show_detail(self, _event: object = None) -> None:
        selected = self.table.selection()
        if not selected: return
        item = next(row for row in self.results if row.ticker == selected[0])
        self.status.set(f"{item.ticker} — score {rebound_score(item)}/100. Review the earnings release and call for: {item.risk_note}. Confirm actual contract bid–ask spreads and that expiration remains within your DTE range.")

    def import_csv(self) -> None:
        filename = filedialog.askopenfilename(title="Load candidate CSV", filetypes=[("CSV files", "*.csv")])
        if not filename: return
        try:
            self.candidates = load_candidates(Path(filename))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not load CSV", str(exc)); return
        self.source_label.set(f"Loaded: {Path(filename).name}"); self.apply_screen()

    def export_csv(self) -> None:
        if not hasattr(self, "results"): return
        filename = filedialog.asksaveasfilename(title="Export screened candidates", defaultextension=".csv", initialfile="screened_candidates.csv", filetypes=[("CSV files", "*.csv")])
        if not filename: return
        with Path(filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ticker", "company", "drop_pct", "low_hold_pct", "iv_percentile", "call_open_interest", "dte", "rebound_score", "risk_note"])
            for item in self.results:
                writer.writerow([item.ticker, item.company, item.drop_pct, item.low_hold_pct, item.iv_percentile, item.call_open_interest, item.dte, rebound_score(item), item.risk_note])
        self.status.set(f"Exported {len(self.results)} candidate(s) to {Path(filename).name}")


    def connect_alpaca(self) -> None:
        try:
            self.alpaca = AlpacaMarketData.from_config()
        except ValueError as exc:
            messagebox.showerror("Alpaca Basic", str(exc))
            return
        self.status.set("Alpaca Basic ready: read-only IEX equity data. No orders are supported.")

    def run_backtest(self) -> None:
        if not self.alpaca:
            messagebox.showinfo("Connect Alpaca", "Connect Alpaca Basic first. The backtest uses read-only daily underlying bars.")
            return
        ticker = simpledialog.askstring("Dip-recovery backtest", "Underlying ticker (for example: PGR):", parent=self.master)
        if not ticker:
            return
        try:
            min_dip = float(self.min_drop.get())
            self.status.set(f"Loading 2 years of daily bars for {ticker.upper()}...")
            self.master.update_idletasks()
            bars = self.alpaca.daily_bars(ticker, date.today() - timedelta(days=730), date.today())
            result = run_dip_recovery_proxy(bars, min_dip)
        except Exception as exc:
            messagebox.showerror("Could not run backtest", str(exc))
            return
        self.show_backtest(ticker.upper(), result, min_dip)
        self.status.set(f"Backtest completed for {ticker.upper()}: {len(result.trades)} proxy trades.")

    def show_backtest(self, ticker: str, result: BacktestResult, min_dip: float) -> None:
        window = tk.Toplevel(self.master)
        window.title(f"{ticker} - dip-recovery proxy backtest")
        window.minsize(760, 420)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{ticker} dip-recovery proxy backtest", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"Signal: close-to-close dip ≥ {min_dip:.1f}%; entry: next open; 15 trading-day hold; 10% stop; 12% target. Underlying-price proxy only—not option P&L.").pack(anchor="w", pady=(2, 6))
        ttk.Label(frame, text=f"Trades: {len(result.trades)}   Win rate: {result.win_rate:.1f}%   Average return: {result.average_return:.2f}%").pack(anchor="w", pady=(0, 10))
        columns = ("entry", "exit", "entry_price", "exit_price", "return", "reason")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        labels = {"entry":"Entry", "exit":"Exit", "entry_price":"Entry price", "exit_price":"Exit price", "return":"Return", "reason":"Exit reason"}
        for column in columns:
            tree.heading(column, text=labels[column]); tree.column(column, width=110, anchor="e")
        tree.column("entry", anchor="w"); tree.column("exit", anchor="w"); tree.column("reason", width=120, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        bar.pack(side="right", fill="y"); tree.configure(yscrollcommand=bar.set)
        for trade in result.trades:
            tree.insert("", "end", values=(trade.entry_day, trade.exit_day, f"{trade.entry_price:.2f}", f"{trade.exit_price:.2f}", f"{trade.return_pct:.2f}%", trade.exit_reason))

    def screen_watchlist(self) -> None:
        if not self.alpaca:
            messagebox.showinfo("Connect Alpaca", "Connect Alpaca Basic first. The watchlist screen uses its read-only daily bars.")
            return
        try:
            minimum_drop = float(self.min_drop.get())
            watchlist = load_watchlist(WATCHLIST)
            research: list[DropResearch] = []
            self.status.set(f"Scanning {len(watchlist)} watchlist tickers for large drops...")
            self.master.update_idletasks()
            for item in watchlist:
                bars = self.alpaca.daily_bars(item.ticker, date.today() - timedelta(days=120), date.today())
                candidate = latest_large_drop(item, bars, minimum_drop)
                if candidate: research.append(candidate)
        except Exception as exc:
            messagebox.showerror("Could not screen watchlist", str(exc))
            return
        self.show_drop_research(research, minimum_drop)
        self.status.set(f"Watchlist scan completed: {len(research)} of {len(watchlist)} tickers had a qualifying drop in the 120-day lookback.")

    def show_drop_research(self, research: list[DropResearch], minimum_drop: float) -> None:
        window = tk.Toplevel(self.master)
        window.title("Large single-day drop research")
        window.minsize(820, 420)
        frame = ttk.Frame(window, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Large single-day drop research", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"Watchlist results from the last 120 calendar days. Signal: close-to-close drop of at least {minimum_drop:.1f}%. IEX daily bars; not an earnings-only screen.").pack(anchor="w", pady=(2, 10))
        columns = ("ticker", "company", "signal", "drop", "signal_close", "latest_close", "recovery")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        labels = {"ticker":"Ticker", "company":"Company", "signal":"Drop date", "drop":"One-day drop", "signal_close":"Signal close", "latest_close":"Latest close", "recovery":"Since-signal return"}
        for column in columns:
            tree.heading(column, text=labels[column]); tree.column(column, width=110, anchor="e")
        tree.column("ticker", width=70, anchor="w"); tree.column("company", width=170, anchor="w"); tree.column("signal", anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); bar.pack(side="right", fill="y"); tree.configure(yscrollcommand=bar.set)
        for item in sorted(research, key=lambda row: row.drop_pct):
            tree.insert("", "end", values=(item.ticker, item.company, item.signal_day, f"{item.drop_pct:.2f}%", f"{item.signal_close:.2f}", f"{item.latest_close:.2f}", f"{item.recovery_pct:.2f}%"))

    def compare_strategies(self) -> None:
        if not self.alpaca:
            messagebox.showinfo("Connect Alpaca", "Connect Alpaca Basic first. Strategy comparison uses its read-only daily bars.")
            return
        try:
            minimum_drop = float(self.min_drop.get())
            watchlist = load_watchlist(WATCHLIST)
            data = {}
            self.status.set(f"Loading two years of bars for {len(watchlist)} watchlist tickers...")
            self.master.update_idletasks()
            for item in watchlist:
                data[item.ticker] = self.alpaca.daily_bars(item.ticker, date.today() - timedelta(days=730), date.today())
            results = {strategy.name: [trade for bars in data.values() for trade in run_strategy(bars, minimum_drop, strategy).trades] for strategy in STRATEGIES}
        except Exception as exc:
            messagebox.showerror("Could not compare strategies", str(exc))
            return
        self.show_strategy_comparison(results, minimum_drop, len(watchlist))
        self.status.set("Strategy comparison completed across the current watchlist.")

    def show_strategy_comparison(self, results: dict[str, list], minimum_drop: float, universe_size: int) -> None:
        window = tk.Toplevel(self.master)
        window.title("Dip-recovery strategy comparison")
        window.minsize(760, 370)
        frame = ttk.Frame(window, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Dip-recovery strategy comparison", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"Two years of IEX daily bars across {universe_size} watchlist tickers. Signal: one-day close-to-close drop ≥ {minimum_drop:.1f}%. Underlying-price proxy, not option P&L.").pack(anchor="w", pady=(2, 10))
        columns = ("strategy", "trades", "wins", "win_rate", "average", "description")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        labels = {"strategy":"Strategy", "trades":"Trades", "wins":"Wins", "win_rate":"Win rate", "average":"Average return", "description":"Rules"}
        for column in columns:
            tree.heading(column, text=labels[column]); tree.column(column, width=100, anchor="e")
        tree.column("strategy", width=150, anchor="w"); tree.column("description", width=300, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); bar.pack(side="right", fill="y"); tree.configure(yscrollcommand=bar.set)
        for strategy in STRATEGIES:
            trades = results[strategy.name]
            wins = sum(trade.return_pct > 0 for trade in trades)
            win_rate = wins / len(trades) * 100 if trades else 0
            average = sum(trade.return_pct for trade in trades) / len(trades) if trades else 0
            tree.insert("", "end", values=(strategy.name, len(trades), wins, f"{win_rate:.1f}%", f"{average:.2f}%", strategy.description))


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root); style.configure("Title.TLabel", font=("Segoe UI", 18, "bold")); style.configure("Muted.TLabel", foreground="#666666")
    RecoveryTrader(root)
    root.mainloop()


if __name__ == "__main__": main()
