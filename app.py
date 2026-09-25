import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(
    page_title="CMC Multi-Product Tracker",
    page_icon="📈",
    layout="wide",
)

st.title("📈 CMC Markets – Multi-Product Performance Tracker")
st.caption("Account performance and trading costs for the selected date range")
st.markdown("---")

uploaded_file = st.sidebar.file_uploader(
    "📂 Upload your CMC CSV file",
    type=["csv", "xlsx"],
)

if uploaded_file is None:
    st.info("👈 Upload your CMC Markets history CSV to begin.")
    st.stop()


# ====================== HELPERS ======================
def clean_money(series, fill_value=None):
    """Convert CMC money strings to numbers; preserve missing values by default."""
    if series is None:
        return pd.Series(dtype=float)

    s = series.astype("string").str.strip()
    s = s.replace({"": pd.NA, "-": pd.NA, "–": pd.NA, "—": pd.NA})

    s = s.str.replace(",", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.str.replace(r"(?i)(SGD|USD|S\$)", "", regex=True)
    s = s.str.replace("$", "", regex=False)
    s = s.str.replace("£", "", regex=False)
    s = s.str.replace("€", "", regex=False)
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)

    result = pd.to_numeric(s, errors="coerce")
    if fill_value is not None:
        result = result.fillna(fill_value)

    return result


def find_col(df, possible_names):
    """Find a column by name, ignoring case and surrounding whitespace."""
    normalized = {
        str(col).strip().casefold(): col
        for col in df.columns
    }

    for name in possible_names:
        key = str(name).strip().casefold()
        if key in normalized:
            return normalized[key]

    return None


def money_text(value):
    if value is None or pd.isna(value):
        return "N/A"
    return f"SGD {float(value):,.2f}"


# ====================== READ FILE ======================
try:
    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file, low_memory=False)
    else:
        df = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"Could not read file: {e}")
    st.stop()

if df.empty:
    st.error("The uploaded file is empty.")
    st.stop()

df.columns = [str(col).strip() for col in df.columns]
df["_source_order"] = range(len(df))

# Column detection
col_date = find_col(df, ["DATE/TIME", "Date/Time", "Date"])
col_type = find_col(df, ["TYPE", "Type"])
col_product = find_col(df, ["PRODUCT", "Product", "Instrument", "Symbol"])
col_amount = find_col(df, ["AMOUNT (SGD)", "Amount (SGD)", "Amount"])
col_balance = find_col(df, ["BALANCE (SGD)", "Balance (SGD)", "Balance"])
col_holding = find_col(
    df,
    ["HOLDING COST (SGD)", "Holding Cost (SGD)", "HOLDING COST - AMOUNT"],
)
col_holding_total = find_col(
    df,
    ["HOLDING COST - TOTAL (SGD)", "HOLDING COST - TOTAL"],
)

if not all([col_date, col_type, col_product, col_amount]):
    st.error("Missing critical columns.")
    st.write(list(df.columns))
    st.stop()

# ====================== CLEAN DATA ======================
# Amount is a transaction cashflow; missing values can be treated as zero.
df[col_amount] = clean_money(df[col_amount], fill_value=0.0)

# Missing balances and holding-cost details must remain missing, not become zero.
if col_balance:
    df[col_balance] = clean_money(df[col_balance])

if col_holding:
    df[col_holding] = clean_money(df[col_holding])

if col_holding_total:
    df[col_holding_total] = clean_money(df[col_holding_total])

df[col_date] = pd.to_datetime(
    df[col_date],
    errors="coerce",
    dayfirst=True,
)

df = df.dropna(subset=[col_date]).copy()

if df.empty:
    st.error("No valid dates were found in the uploaded file.")
    st.stop()

# CMC exports are commonly newest-first. Sort chronologically and retain the
# original row order as a tie-breaker for rows with the same timestamp.
df = (
    df.sort_values([col_date, "_source_order"], kind="mergesort")
    .reset_index(drop=True)
)

df[col_product] = (
    df[col_product]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.upper()
    .str.replace(r"\s+", " ", regex=True)
    .str.replace(r"\s*/\s*", "/", regex=True)
    .replace({"-": ""})
)

df[col_type] = df[col_type].fillna("").astype(str).str.strip()


# ====================== DATE FILTER ======================
st.sidebar.header("🎛️ Filters")

min_date = df[col_date].min().date()
max_date = df[col_date].max().date()

date_range = st.sidebar.date_input(
    "📅 Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

if not isinstance(date_range, (tuple, list)) or len(date_range) != 2:
    st.info("Select both a start date and an end date.")
    st.stop()

start_date, end_date = date_range

if start_date > end_date:
    st.error("The start date must be on or before the end date.")
    st.stop()

start_ts = pd.Timestamp(start_date)
end_exclusive = pd.Timestamp(end_date) + pd.Timedelta(days=1)

period_mask = (
    (df[col_date] >= start_ts)
    & (df[col_date] < end_exclusive)
)

df_period = df.loc[period_mask].copy()
type_period = df_period[col_type].str.casefold()


# ====================== PERIOD CASHFLOWS ======================
deposit_mask = type_period.str.contains(
    r"payment in|deposit",
    na=False,
)
withdrawal_mask = type_period.str.contains(
    r"payment out|withdraw",
    na=False,
)

# Normalize external cashflows by transaction type:
# deposits increase the account; withdrawals reduce it.
period_deposits = float(
    df_period.loc[deposit_mask, col_amount].abs().sum()
)
period_withdrawals = float(
    df_period.loc[withdrawal_mask, col_amount].abs().sum()
)
net_external_flows = period_deposits - period_withdrawals


# ====================== PERIOD BALANCE / RETURN ======================
opening_balance = None
ending_balance = None
opening_note = "Unavailable"

if col_balance:
    balance_history = df.loc[
        df[col_balance].notna(),
        [col_date, col_type, col_balance],
    ].copy()

    # Opening snapshot: the last known balance strictly before the first
    # selected calendar day.
    balances_before_start = balance_history.loc[
        balance_history[col_date] < start_ts
    ]

    if not balances_before_start.empty:
        opening_balance = float(
            balances_before_start.iloc[-1][col_balance]
        )
        opening_note = "Last recorded balance before the selected range"

    elif not balance_history.empty:
        # If the uploaded history starts with a deposit, assume the account
        # balance immediately before that initial deposit was zero.
        first_balance_row = balance_history.iloc[0]
        first_balance_type = str(first_balance_row[col_type]).casefold()
        first_data_day = df[col_date].min().normalize()

        starts_with_deposit = bool(
            re.search(r"payment in|deposit", first_balance_type)
        )

        if start_ts.normalize() <= first_data_day and starts_with_deposit:
            opening_balance = 0.0
            opening_note = (
                "Assumed SGD 0.00 before the first recorded deposit"
            )

    # Ending snapshot: the last known balance on or before the selected
    # end date. This still works if there was no balance-changing event on
    # the final day of the selected range.
    balances_through_end = balance_history.loc[
        balance_history[col_date] < end_exclusive
    ]

    if not balances_through_end.empty:
        ending_balance = float(
            balances_through_end.iloc[-1][col_balance]
        )


st.sidebar.markdown("---")
st.sidebar.subheader("💰 Period Return Basis")

capital_method = st.sidebar.radio(
    "Opening balance method:",
    ["Smart (Recommended)", "Manual Input"],
    index=0,
)

if capital_method == "Manual Input":
    manual_default = (
        float(opening_balance)
        if opening_balance is not None
        else 2000.00
    )

    opening_balance_for_calc = st.sidebar.number_input(
        "Opening balance before selected range (SGD)",
        min_value=0.0,
        value=float(round(manual_default, 2)),
        step=50.0,
        format="%.2f",
    )
    opening_note_for_calc = "Manual opening balance"
else:
    opening_balance_for_calc = opening_balance
    opening_note_for_calc = opening_note

capital_base = None
period_net_account_pnl = None
period_return_pct = None

if opening_balance_for_calc is not None:
    capital_base = float(opening_balance_for_calc + net_external_flows)

    if ending_balance is not None:
        period_net_account_pnl = float(
            ending_balance
            - opening_balance_for_calc
            - net_external_flows
        )

        if capital_base > 0:
            period_return_pct = (
                period_net_account_pnl / capital_base * 100
            )


# ====================== CLOSED TRADES ======================
is_close = type_period.str.contains("close trade", na=False)
has_product = df_period[col_product].str.len() > 0

trades = df_period.loc[is_close & has_product].copy()
trades["PnL"] = trades[col_amount]
total_pnl = float(trades["PnL"].sum())


# ====================== TRADING COSTS ======================
is_commission = type_period.str.contains(
    "commission charge",
    na=False,
)
commission_rows = df_period.loc[is_commission].copy()

# Amount is the actual account cashflow. In this export, commission charges
# are usually negative, so the expense-positive cost is the negative of it.
commission_cashflow = float(
    commission_rows[col_amount].sum()
)
commission_net_cost = -commission_cashflow

is_holding = type_period.str.contains("holding cost", na=False)
holding_rows = df_period.loc[is_holding].copy()

if not holding_rows.empty:
    is_account_holding = holding_rows[col_product].eq("ACCOUNT")
    account_holding_rows = holding_rows.loc[is_account_holding].copy()

    # An ACCOUNT holding row is the posted net cash movement for that
    # timestamp. Do not add the position-level breakdown for that same time.
    account_holding_timestamps = set(
        account_holding_rows[col_date].tolist()
    )

    detail_holding_rows = holding_rows.loc[
        (~is_account_holding)
        & (~holding_rows[col_date].isin(account_holding_timestamps))
    ].copy()
else:
    account_holding_rows = holding_rows.copy()
    detail_holding_rows = holding_rows.copy()


def detail_holding_cashflow(row):
    # Position-level holding rows usually have their cash amount in
    # HOLDING COST (SGD). Use other columns only as fallbacks.
    for candidate in [col_holding, col_holding_total]:
        if candidate is not None:
            value = row.get(candidate)
            if pd.notna(value):
                return float(value)

    amount_value = row.get(col_amount)
    if pd.notna(amount_value):
        return float(amount_value)

    return 0.0


if not detail_holding_rows.empty:
    detail_holding_rows["_HoldingCashflow"] = detail_holding_rows.apply(
        detail_holding_cashflow,
        axis=1,
    )

account_holding_cashflow = float(
    account_holding_rows[col_amount].sum()
)

detail_holding_cashflow_total = (
    float(detail_holding_rows["_HoldingCashflow"].sum())
    if not detail_holding_rows.empty
    else 0.0
)

holding_cashflow = (
    account_holding_cashflow + detail_holding_cashflow_total
)

# Positive means an expense; negative means a net credit/refund.
holding_net_cost = -holding_cashflow
total_net_costs = commission_net_cost + holding_net_cost


# ====================== 1. ACCOUNT SUMMARY ======================
st.subheader("💰 Account Summary (Selected Date Range)")

s1, s2, s3, s4, s5 = st.columns(5)

s1.metric(
    "🏦 Balance at Period End",
    money_text(ending_balance),
)

s2.metric(
    "🚀 Starting Capital / Capital Base",
    money_text(capital_base),
)

s3.metric(
    "📈 Closed-Trade P&L (Gross)",
    money_text(total_pnl),
)

s4.metric(
    "💼 Net Account P&L",
    money_text(period_net_account_pnl),
)

if period_return_pct is None:
    s5.metric("📊 Net Account Return", "N/A")
else:
    s5.metric(
        "📊 Net Account Return",
        f"{period_return_pct:+.2f}%",
        delta="Increase" if period_return_pct >= 0 else "Decrease",
    )

st.caption(
    f"Opening balance method: {opening_note_for_calc}. "
    "Net Account P&L = ending balance − opening balance − deposits "
    "+ withdrawals. Starting Capital / Capital Base = opening balance "
    "+ deposits − withdrawals in the selected range."
)

if opening_note_for_calc.startswith("Assumed"):
    st.warning(
        "The uploaded history appears to begin with a deposit, so the app "
        "assumes the balance before that deposit was SGD 0.00. Verify that "
        "the file contains the full account history from its first deposit."
    )

st.caption(
    f"Selected-range deposits: **{money_text(period_deposits)}** · "
    f"withdrawals: **{money_text(period_withdrawals)}**"
)

if capital_base is not None and capital_base <= 0:
    st.warning(
        "The period capital base is zero or negative, so a percentage "
        "return cannot be calculated."
    )

st.markdown("---")


# ====================== 2. TRADING COSTS ======================
st.subheader("💸 Trading Costs (Selected Date Range)")

c1, c2, c3 = st.columns(3)

c1.metric(
    "💳 Commission Net Cost",
    money_text(commission_net_cost),
)

c2.metric(
    "🌙 Holding Cost (Net)",
    money_text(holding_net_cost),
)

c3.metric(
    "📉 Net Costs",
    money_text(total_net_costs),
)

st.caption(
    "Cost convention: positive = net expense; negative = net credit. "
    "CMC's ACCOUNT holding row is used when present; position-level "
    "holding rows are used only when an ACCOUNT row is absent for that "
    "timestamp."
)

# Build a table whose net-cost column sums to the net-cost metrics.
def build_cost_part(source_rows, cost_type, cashflow_values):
    part = source_rows[[col_date, col_type, col_product]].copy()
    cashflow_values = pd.Series(
        cashflow_values,
        index=source_rows.index,
        dtype="float64",
    )

    part["Cost Type"] = cost_type
    part["Cashflow (SGD)"] = cashflow_values.to_numpy()
    part["Cost / (Credit) (SGD)"] = -part["Cashflow (SGD)"]
    return part


with st.expander(
    "🔍 See actual cost rows (newest first)",
    expanded=False,
):
    cost_parts = []

    if not commission_rows.empty:
        cost_parts.append(
            build_cost_part(
                commission_rows,
                "Commission",
                commission_rows[col_amount],
            )
        )

    if not account_holding_rows.empty:
        cost_parts.append(
            build_cost_part(
                account_holding_rows,
                "Holding (Account total)",
                account_holding_rows[col_amount],
            )
        )

    if not detail_holding_rows.empty:
        cost_parts.append(
            build_cost_part(
                detail_holding_rows,
                "Holding (detail fallback)",
                detail_holding_rows["_HoldingCashflow"],
            )
        )

    if cost_parts:
        all_costs = pd.concat(cost_parts, ignore_index=True)
        all_costs = all_costs.sort_values(
            col_date,
            ascending=False,
            kind="mergesort",
        ).reset_index(drop=True)

        all_costs = all_costs.rename(
            columns={
                col_date: "Date/Time",
                col_type: "Transaction",
                col_product: "Product",
            }
        )

        all_costs = all_costs[
            [
                "Date/Time",
                "Cost Type",
                "Transaction",
                "Product",
                "Cashflow (SGD)",
                "Cost / (Credit) (SGD)",
            ]
        ]

        st.dataframe(
            all_costs,
            use_container_width=True,
            height=400,
        )

        table_total = float(all_costs["Cost / (Credit) (SGD)"].sum())
        st.write(
            f"**Sum of Cost / (Credit) column = Net Costs: "
            f"{money_text(table_total)}**"
        )
    else:
        st.info("No commission or holding-cost rows in this date range.")

st.markdown("---")


# ====================== 3. PERFORMANCE BY PRODUCT ======================
if trades.empty:
    st.warning("No closed trades found in the selected date range.")
    st.stop()

all_products = sorted(trades[col_product].unique().tolist())

selected_products = st.sidebar.multiselect(
    "🌐 Products to include in trade analysis",
    all_products,
    default=all_products,
)

filtered = trades.loc[
    trades[col_product].isin(selected_products)
].copy()

if filtered.empty:
    st.warning("No closed trades match the selected products.")
    st.stop()

n_trades = len(filtered)
wins = int((filtered["PnL"] > 0).sum())
losses = int((filtered["PnL"] < 0).sum())
breakeven = int((filtered["PnL"] == 0).sum())

win_rate = wins / n_trades * 100 if n_trades else 0.0

avg_win = (
    filtered.loc[filtered["PnL"] > 0, "PnL"].mean()
    if wins > 0
    else 0.0
)
avg_loss = (
    filtered.loc[filtered["PnL"] < 0, "PnL"].mean()
    if losses > 0
    else 0.0
)

st.subheader("📊 Trading Performance Summary")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Closed Trades", n_trades)
k2.metric(
    "Win Rate",
    f"{win_rate:.1f}%",
    f"{wins}W / {losses}L / {breakeven}BE",
)
k3.metric("Average Win", money_text(avg_win))
k4.metric("Average Loss", money_text(avg_loss))

st.divider()

st.subheader("🏆 Which Product Makes (or Loses) the Most Money?")

stats = (
    filtered.groupby(col_product)
    .agg(
        Total_PnL=("PnL", "sum"),
        Trades=("PnL", "count"),
        Wins=("PnL", lambda x: (x > 0).sum()),
        Losses=("PnL", lambda x: (x < 0).sum()),
        Avg_PnL=("PnL", "mean"),
        Best=("PnL", "max"),
        Worst=("PnL", "min"),
    )
    .reset_index()
)

stats["Win_Rate_%"] = (
    stats["Wins"] / stats["Trades"] * 100
).round(1)

stats = stats.sort_values("Total_PnL", ascending=False)

show = stats[
    [
        col_product,
        "Total_PnL",
        "Trades",
        "Win_Rate_%",
        "Avg_PnL",
        "Best",
        "Worst",
    ]
].copy()

show.columns = [
    "Product",
    "Total P&L (SGD)",
    "Trades",
    "Win Rate %",
    "Avg P&L (SGD)",
    "Best Trade (SGD)",
    "Worst Trade (SGD)",
]

st.dataframe(show, use_container_width=True)

fig_bar = go.Figure(
    go.Bar(
        y=stats[col_product],
        x=stats["Total_PnL"],
        orientation="h",
        marker_color=[
            "#00CC96" if value >= 0 else "#EF553B"
            for value in stats["Total_PnL"]
        ],
        text=stats["Total_PnL"].apply(
            lambda value: f"{value:,.2f}"
        ),
        textposition="outside",
    )
)

fig_bar.update_layout(
    height=max(400, len(stats) * 45),
    yaxis={"categoryorder": "total ascending"},
)
fig_bar.add_vline(x=0, line_width=2, line_color="black")
st.plotly_chart(fig_bar, use_container_width=True)

st.divider()


# ====================== 4. ONE-PRODUCT DEEP DIVE ======================
st.subheader("🔍 Deep Dive – One Product")

product_choice = st.selectbox("Select product", all_products)
one = filtered.loc[filtered[col_product] == product_choice].copy()

if not one.empty:
    one_pnl = float(one["PnL"].sum())
    one_wr = float((one["PnL"] > 0).mean() * 100)

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("P&L", money_text(one_pnl))
    d2.metric("Win Rate", f"{one_wr:.1f}%")
    d3.metric("Trades", len(one))
    d4.metric("Average per Trade", money_text(one_pnl / len(one)))

    one = one.sort_values(col_date)
    one["Cumulative"] = one["PnL"].cumsum()

    fig_one = px.area(
        one,
        x=col_date,
        y="Cumulative",
        title=f"Equity Curve – {product_choice}",
    )
    fig_one.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig_one, use_container_width=True)

st.divider()


# ====================== 5. OVERALL EQUITY CURVE ======================
st.subheader("📈 Overall Equity Curve (Closed Trades Only)")

filtered = filtered.sort_values(col_date).copy()
filtered["Cumulative"] = filtered["PnL"].cumsum()

fig_eq = px.area(
    filtered,
    x=col_date,
    y="Cumulative",
    color_discrete_sequence=["#636EFA"],
)
fig_eq.add_hline(y=0, line_dash="dash", line_color="gray")
st.plotly_chart(fig_eq, use_container_width=True)


# ====================== 6. CLOSED TRADES LOG ======================
st.subheader("📋 Closed Trades Log")

log = filtered[[col_date, col_product, "PnL"]].sort_values(
    col_date,
    ascending=False,
)

log.columns = ["Date/Time", "Product", "P&L (SGD)"]
st.dataframe(log, use_container_width=True, height=300)

csv = log.to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Download filtered trades",
    csv,
    "cmc_closed_trades.csv",
    "text/csv",
)

st.info(
    "Account balance, return, closed-trade P&L, and costs use the "
    "selected date range. The product selector affects the trade-analysis "
    "sections only."
)