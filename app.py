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


# ====================== HELPERS ======================
def clean_money(series, fill_value=None):
    """Convert CMC money strings to numbers."""
    if series is None:
        return pd.Series(dtype=float)

    s = series.astype("string").str.strip()
    s = s.replace(
        {
            "": pd.NA,
            "-": pd.NA,
            "–": pd.NA,
            "—": pd.NA,
        }
    )

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
    """Format currency without forcing a positive or negative sign."""
    if value is None or pd.isna(value):
        return "N/A"

    return f"SGD {float(value):,.2f}"


def signed_money_text(value):
    """Format currency with + for positive and - for negative."""
    if value is None or pd.isna(value):
        return "N/A"

    value = round(float(value), 2)

    if value > 0:
        return f"SGD +{value:,.2f}"
    if value < 0:
        return f"SGD -{abs(value):,.2f}"

    return "SGD 0.00"


# ====================== UPLOAD / READ FILE ======================
uploaded_file = st.sidebar.file_uploader(
    "📂 Upload your CMC CSV file",
    type=["csv", "xlsx"],
)

if uploaded_file is None:
    st.info("👈 Upload your CMC Markets history CSV to begin.")
    st.stop()

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
df["__source_order__"] = range(len(df))


# ====================== COLUMN DETECTION ======================
col_date = find_col(df, ["DATE/TIME", "Date/Time", "Date"])
col_type = find_col(df, ["TYPE", "Type"])
col_product = find_col(
    df,
    ["PRODUCT", "Product", "Instrument", "Symbol"],
)
col_amount = find_col(
    df,
    ["AMOUNT (SGD)", "Amount (SGD)", "Amount"],
)
col_balance = find_col(
    df,
    ["BALANCE (SGD)", "Balance (SGD)", "Balance"],
)
col_holding = find_col(
    df,
    [
        "HOLDING COST (SGD)",
        "Holding Cost (SGD)",
        "HOLDING COST - AMOUNT",
    ],
)
col_holding_total = find_col(
    df,
    [
        "HOLDING COST - TOTAL (SGD)",
        "HOLDING COST - TOTAL",
    ],
)

if not all([col_date, col_type, col_product, col_amount]):
    st.error("Missing critical columns.")
    st.write(list(df.columns))
    st.stop()


# ====================== CLEAN DATA ======================
# Missing transaction amounts are treated as zero.
df[col_amount] = clean_money(df[col_amount], fill_value=0.0)

# Missing balances and holding details remain missing.
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


# ====================== SORT ROWS CHRONOLOGICALLY ======================
# CMC files are often newest-first. Detect the overall file order so that rows
# with identical timestamps can also be put into chronological order.
date_sequence = df[col_date]
date_deltas = date_sequence.diff().dropna()

increasing_steps = int(
    (date_deltas > pd.Timedelta(0)).sum()
)
decreasing_steps = int(
    (date_deltas < pd.Timedelta(0)).sum()
)

if decreasing_steps > increasing_steps:
    source_is_newest_first = True
elif increasing_steps > decreasing_steps:
    source_is_newest_first = False
else:
    # Use the first and last valid dates when the direction is ambiguous.
    source_is_newest_first = (
        len(date_sequence) > 1
        and date_sequence.iloc[0] > date_sequence.iloc[-1]
    )

# For a newest-first source file, reverse original row order within identical
# timestamps to get chronological order. For an oldest-first file, retain it.
df = (
    df.sort_values(
        [col_date, "__source_order__"],
        ascending=[True, not source_is_newest_first],
        kind="mergesort",
    )
    .reset_index(drop=True)
)

source_order_description = (
    "newest-first"
    if source_is_newest_first
    else "oldest-first or ambiguous"
)


# Normalize product and transaction text.
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

df[col_type] = (
    df[col_type]
    .fillna("")
    .astype(str)
    .str.strip()
)


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


# ====================== TRANSACTION TYPE MASKS ======================
is_deposit = type_period.str.contains(
    r"payment\s+in|deposit",
    na=False,
)

is_withdrawal = type_period.str.contains(
    r"payment\s+out|withdraw",
    na=False,
)

is_close = type_period.str.contains(
    "close trade",
    na=False,
)

is_commission = type_period.str.contains(
    "commission charge",
    na=False,
)

is_holding = type_period.str.contains(
    "holding cost",
    na=False,
)


# ====================== DEPOSITS / WITHDRAWALS ======================
# Deposits and withdrawals are external flows, not trading P&L.
period_deposits = float(
    df_period.loc[is_deposit, col_amount].abs().sum()
)

period_withdrawals = float(
    df_period.loc[is_withdrawal, col_amount].abs().sum()
)

net_external_flows = period_deposits - period_withdrawals


# ====================== OPENING / ENDING BALANCES ======================
opening_balance = None
ending_balance = None
opening_note = "Unavailable"
ending_balance_row = None
balance_history = pd.DataFrame()

if col_balance:
    balance_history = df.loc[
        df[col_balance].notna(),
        [col_date, col_type, col_product, col_amount, col_balance],
    ].copy()

    # Opening balance is the last known account balance before the selected
    # start date.
    balances_before_start = balance_history.loc[
        balance_history[col_date] < start_ts
    ]

    if not balances_before_start.empty:
        opening_balance_row = balances_before_start.iloc[-1]
        opening_balance = float(opening_balance_row[col_balance])
        opening_note = "Last recorded balance before the selected range"

    elif not balance_history.empty:
        # If the uploaded history starts with a deposit on the selected
        # start date, assume the account balance before that deposit was 0.
        first_balance_row = balance_history.iloc[0]
        first_type = str(first_balance_row[col_type]).casefold()
        first_data_day = df[col_date].min().normalize()

        starts_with_deposit = bool(
            re.search(r"payment\s+in|deposit", first_type)
        )

        if (
            start_ts.normalize() <= first_data_day
            and starts_with_deposit
        ):
            opening_balance = 0.0
            opening_note = (
                "Assumed SGD 0.00 before the first recorded deposit"
            )

    # Ending balance is the last chronological balance snapshot on or before
    # the selected end date. The sort above handles equal timestamps.
    balances_through_end = balance_history.loc[
        balance_history[col_date] < end_exclusive
    ]

    if not balances_through_end.empty:
        ending_balance_row = balances_through_end.iloc[-1]
        ending_balance = float(ending_balance_row[col_balance])


# ====================== OPENING BALANCE METHOD ======================
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


# ====================== ACCOUNT RETURN CALCULATIONS ======================
capital_base = None
period_net_account_pnl = None
period_return_pct = None

if opening_balance_for_calc is not None:
    capital_base = float(
        opening_balance_for_calc
        + period_deposits
        - period_withdrawals
    )

    if ending_balance is not None:
        period_net_account_pnl = float(
            ending_balance
            - opening_balance_for_calc
            - period_deposits
            + period_withdrawals
        )

        if capital_base > 0:
            period_return_pct = (
                period_net_account_pnl / capital_base * 100
            )


# ====================== CLOSED-TRADE P&L ======================
close_rows = df_period.loc[is_close].copy()

# Use all Close Trade amounts for account reconciliation.
gross_closed_trade_pnl = float(
    close_rows[col_amount].sum()
)

# Product analysis needs a product value for each trade.
has_product = df_period[col_product].str.len() > 0

trades = df_period.loc[is_close & has_product].copy()
trades["PnL"] = trades[col_amount]

close_rows_without_product = df_period.loc[
    is_close & ~has_product
].copy()

if not close_rows_without_product.empty:
    missing_product_pnl = float(
        close_rows_without_product[col_amount].sum()
    )
else:
    missing_product_pnl = 0.0


# ====================== COMMISSION COSTS ======================
commission_rows = df_period.loc[is_commission].copy()

# Preserve CMC's cashflow sign:
# negative = expense; positive = credit.
commission_cashflow = float(
    commission_rows[col_amount].sum()
)


# ====================== HOLDING COSTS ======================
holding_rows = df_period.loc[is_holding].copy()

if not holding_rows.empty:
    is_account_holding = holding_rows[col_product].eq("ACCOUNT")

    account_holding_rows = holding_rows.loc[
        is_account_holding
    ].copy()

    account_holding_timestamps = set(
        account_holding_rows[col_date].tolist()
    )

    # If an Account holding row exists for a timestamp, it represents the
    # posted total. Do not add the position breakdown from that timestamp.
    detail_holding_rows = holding_rows.loc[
        (~is_account_holding)
        & (~holding_rows[col_date].isin(account_holding_timestamps))
    ].copy()
else:
    account_holding_rows = holding_rows.copy()
    detail_holding_rows = holding_rows.copy()


def get_detail_holding_cashflow(row):
    """Read a holding detail while preserving its positive/negative sign."""
    candidates = [col_holding, col_holding_total]

    # Prefer a non-zero detailed holding value.
    for candidate in candidates:
        if candidate is None:
            continue

        value = row.get(candidate)

        if pd.notna(value) and float(value) != 0:
            return float(value)

    # If the available detail values are zero, retain zero.
    for candidate in candidates:
        if candidate is None:
            continue

        value = row.get(candidate)

        if pd.notna(value):
            return float(value)

    # Final fallback for an unusual export where the holding detail is in
    # Amount rather than a holding-specific column.
    amount_value = row.get(col_amount)

    if pd.notna(amount_value):
        return float(amount_value)

    return 0.0


if not detail_holding_rows.empty:
    detail_holding_rows["_HoldingCashflow"] = (
        detail_holding_rows.apply(
            get_detail_holding_cashflow,
            axis=1,
        )
    )

account_holding_cashflow = float(
    account_holding_rows[col_amount].sum()
)

if not detail_holding_rows.empty:
    detail_holding_cashflow = float(
        detail_holding_rows["_HoldingCashflow"].sum()
    )
else:
    detail_holding_cashflow = 0.0

holding_cashflow = (
    account_holding_cashflow
    + detail_holding_cashflow
)

# The requested cost sign convention is the cashflow sign:
# negative = expense; positive = credit.
net_trading_costs = (
    commission_cashflow
    + holding_cashflow
)


# ====================== OTHER INTERNAL CASHFLOWS ======================
# These are non-zero Amount values whose types are not Close Trade,
# Commission Charge, Holding Cost, deposit, or withdrawal.
classified_mask = (
    is_close
    | is_commission
    | is_holding
    | is_deposit
    | is_withdrawal
)

other_rows = df_period.loc[~classified_mask].copy()

other_internal_cashflow = float(
    other_rows[col_amount].sum()
)

# Because expenses are negative and credits positive:
# gross P&L + signed costs + other internal movements = expected account P&L.
trade_result_after_costs = (
    gross_closed_trade_pnl
    + net_trading_costs
)

ledger_pnl_from_rows = (
    trade_result_after_costs
    + other_internal_cashflow
)

if period_net_account_pnl is not None:
    reconciliation_difference = (
        period_net_account_pnl
        - ledger_pnl_from_rows
    )
else:
    reconciliation_difference = None


# ====================== 1. ACCOUNT SUMMARY ======================
st.subheader("💰 Account Summary (Selected Date Range)")

main_metrics = st.columns(3)

main_metrics[0].metric(
    "🏦 Balance at Period End",
    money_text(ending_balance),
)

main_metrics[1].metric(
    "📈 Closed-Trade P&L (Gross)",
    money_text(gross_closed_trade_pnl),
)

if period_return_pct is None:
    main_metrics[2].metric(
        "📊 Net Account Return",
        "N/A",
    )
else:
    main_metrics[2].metric(
        "📊 Net Account Return",
        f"{period_return_pct:+.2f}%",
        delta=(
            "Increase"
            if period_return_pct >= 0
            else "Decrease"
        ),
    )

st.markdown("##### Supporting figures")

supporting_metrics = st.columns(2)

supporting_metrics[0].metric(
    "🚀 Starting Capital / Capital Base",
    money_text(capital_base),
)

supporting_metrics[1].metric(
    "💼 Net Account P&L",
    money_text(period_net_account_pnl),
)

with st.expander("Period calculation and balance details", expanded=False):
    st.write(f"**Opening balance method:** {opening_note_for_calc}")
    st.write(
        f"**Opening balance used:** "
        f"{money_text(opening_balance_for_calc)}"
    )
    st.caption(
        "Net Account P&L = ending balance − opening balance − deposits "
        "+ withdrawals. Capital Base = opening balance + deposits "
        "− withdrawals."
    )
    st.write(
        f"**Selected-range deposits:** {money_text(period_deposits)} · "
        f"**Withdrawals:** {money_text(period_withdrawals)}"
    )
    st.caption(
        f"Detected source file order: {source_order_description}. "
        "Rows sharing a timestamp are ordered using that source order."
    )

    if opening_note_for_calc.startswith("Assumed"):
        st.warning(
            "The uploaded history appears to begin with a deposit, so the "
            "app assumes a SGD 0.00 balance before that deposit. Verify "
            "that the file contains the full account history from its "
            "first deposit."
        )

    if capital_base is not None and capital_base <= 0:
        st.warning(
            "The period capital base is zero or negative, so a percentage "
            "return cannot be calculated."
        )

    if ending_balance_row is not None:
        last_balance_timestamp = ending_balance_row[col_date]

        same_timestamp_rows = balance_history.loc[
            balance_history[col_date] == last_balance_timestamp
        ].copy()

        if len(same_timestamp_rows) > 1:
            st.write(
                "**Balance snapshots at the final transaction timestamp:**"
            )

            snapshot_display = same_timestamp_rows[
                [
                    col_date,
                    col_type,
                    col_product,
                    col_amount,
                    col_balance,
                ]
            ].copy()

            snapshot_display.columns = [
                "Date/Time",
                "Transaction",
                "Product",
                "Amount (SGD)",
                "Balance (SGD)",
            ]

            st.dataframe(
                snapshot_display,
                use_container_width=True,
            )

            st.caption(
                "The final row in this chronological snapshot group is "
                "the ending balance used in the calculation."
            )

st.markdown("---")


# ====================== 2. TRADING COSTS ======================
st.subheader("💸 Trading Costs (Selected Date Range)")

cost_columns = st.columns(3)

cost_columns[0].metric(
    "💳 Commission",
    signed_money_text(commission_cashflow),
)

cost_columns[1].metric(
    "🌙 Holding Cost (Net)",
    signed_money_text(holding_cashflow),
)

cost_columns[2].metric(
    "📉 Net Costs",
    signed_money_text(net_trading_costs),
)

st.caption(
    "Sign convention: negative = net expense; positive = net credit. "
    "CMC's Account holding row is used when present; position-level "
    "holding rows are used only when an Account row is absent for that "
    "timestamp."
)


def make_cost_table_rows(source_rows, cost_type, cashflows):
    """Create cost-table rows using the signed cashflow."""
    if source_rows.empty:
        return pd.DataFrame(
            columns=[
                "Date/Time",
                "Cost Type",
                "Transaction",
                "Product",
                "Cost / (Credit) (SGD)",
            ]
        )

    part = source_rows[
        [col_date, col_type, col_product]
    ].copy()

    cashflows = pd.Series(
        cashflows,
        index=source_rows.index,
        dtype="float64",
    )

    part["Cost Type"] = cost_type
    part["Cost / (Credit) (SGD)"] = cashflows.to_numpy()

    part = part.rename(
        columns={
            col_date: "Date/Time",
            col_type: "Transaction",
            col_product: "Product",
        }
    )

    return part[
        [
            "Date/Time",
            "Cost Type",
            "Transaction",
            "Product",
            "Cost / (Credit) (SGD)",
        ]
    ]


with st.expander(
    "🔍 See actual cost rows (newest first)",
    expanded=False,
):
    cost_parts = []

    if not commission_rows.empty:
        cost_parts.append(
            make_cost_table_rows(
                commission_rows,
                "Commission",
                commission_rows[col_amount],
            )
        )

    if not account_holding_rows.empty:
        cost_parts.append(
            make_cost_table_rows(
                account_holding_rows,
                "Holding (Account total)",
                account_holding_rows[col_amount],
            )
        )

    if not detail_holding_rows.empty:
        cost_parts.append(
            make_cost_table_rows(
                detail_holding_rows,
                "Holding (detail fallback)",
                detail_holding_rows["_HoldingCashflow"],
            )
        )

    if cost_parts:
        all_costs = pd.concat(
            cost_parts,
            ignore_index=True,
        )

        all_costs = (
            all_costs.sort_values(
                "Date/Time",
                ascending=False,
                kind="mergesort",
            )
            .reset_index(drop=True)
        )

        table_total = float(
            all_costs["Cost / (Credit) (SGD)"].sum()
        )

        cost_display = all_costs.copy()
        cost_display["Cost / (Credit) (SGD)"] = (
            cost_display["Cost / (Credit) (SGD)"]
            .map(signed_money_text)
        )

        st.dataframe(
            cost_display,
            use_container_width=True,
            height=400,
        )

        st.write(
            "**Cost-table total:** "
            f"{signed_money_text(table_total)}"
        )
    else:
        st.info("No commission or holding-cost rows in this date range.")

st.markdown("---")


# ====================== 3. P&L RECONCILIATION ======================
st.subheader("🧾 P&L Reconciliation")

st.caption(
    "With this app's sign convention, compare the figures by adding "
    "signed costs: gross closed-trade P&L + net costs + other internal "
    "cashflows."
)

recon_columns = st.columns(4)

recon_columns[0].metric(
    "Gross Closed-Trade P&L",
    money_text(gross_closed_trade_pnl),
)

recon_columns[1].metric(
    "Net Costs (− expense / + credit)",
    signed_money_text(net_trading_costs),
)

recon_columns[2].metric(
    "Other Internal Cashflows",
    signed_money_text(other_internal_cashflow),
)

recon_columns[3].metric(
    "P&L from Listed Cashflows",
    signed_money_text(ledger_pnl_from_rows),
)

recon_detail_columns = st.columns(2)

recon_detail_columns[0].metric(
    "Balance-Based Net Account P&L",
    money_text(period_net_account_pnl),
)

recon_detail_columns[1].metric(
    "Reconciliation Difference",
    signed_money_text(reconciliation_difference),
)

if reconciliation_difference is None:
    st.info(
        "A reconciliation difference cannot be calculated because the "
        "opening or ending account balance is unavailable."
    )
elif abs(reconciliation_difference) <= 0.02:
    st.success(
        "The balance-based account P&L reconciles with the listed "
        "transaction cashflows, within rounding tolerance."
    )
else:
    st.warning(
        "The balance-based account P&L does not fully reconcile with the "
        "listed cashflows. Check the other-movements breakdown and the "
        "ending-balance snapshots below."
    )

with st.expander(
    "🔎 Other internal cash movements by transaction type",
    expanded=False,
):
    nonzero_other_rows = other_rows.loc[
        other_rows[col_amount].abs() >= 0.005
    ].copy()

    if nonzero_other_rows.empty:
        st.info(
            "No non-zero Amount values were found in unclassified "
            "transaction types for this date range."
        )
    else:
        other_breakdown = (
            nonzero_other_rows.groupby(
                col_type,
                dropna=False,
            )[col_amount]
            .agg(["count", "sum"])
            .reset_index()
        )

        other_breakdown.columns = [
            "Transaction Type",
            "Rows",
            "Cashflow (SGD)",
        ]

        other_breakdown["Cashflow (SGD)"] = (
            other_breakdown["Cashflow (SGD)"]
            .map(signed_money_text)
        )

        st.dataframe(
            other_breakdown,
            use_container_width=True,
        )

st.markdown("---")


# ====================== 4. PERFORMANCE BY PRODUCT ======================
if trades.empty:
    st.warning("No closed trades with a product were found in the selected date range.")
    st.stop()

if abs(missing_product_pnl) >= 0.005:
    st.warning(
        "Some Close Trade rows have no product name. They are included in "
        "the account-wide gross closed-trade P&L and reconciliation, but "
        "not in the product analysis."
    )

all_products = sorted(
    trades[col_product].unique().tolist()
)

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

win_rate = (
    wins / n_trades * 100
    if n_trades
    else 0.0
)

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

performance_columns = st.columns(4)

performance_columns[0].metric(
    "Total Closed Trades",
    n_trades,
)

performance_columns[1].metric(
    "Win Rate",
    f"{win_rate:.1f}%",
    f"{wins}W / {losses}L / {breakeven}BE",
)

performance_columns[2].metric(
    "Average Win",
    money_text(avg_win),
)

performance_columns[3].metric(
    "Average Loss",
    money_text(avg_loss),
)

st.divider()


# ====================== 5. PRODUCT COMPARISON ======================
st.subheader("🏆 Which Product Makes (or Loses) the Most Money?")

stats = (
    filtered.groupby(col_product)
    .agg(
        Total_PnL=("PnL", "sum"),
        Trades=("PnL", "count"),
        Wins=("PnL", lambda values: (values > 0).sum()),
        Losses=("PnL", lambda values: (values < 0).sum()),
        Avg_PnL=("PnL", "mean"),
        Best=("PnL", "max"),
        Worst=("PnL", "min"),
    )
    .reset_index()
)

stats["Win_Rate_%"] = (
    stats["Wins"] / stats["Trades"] * 100
).round(1)

stats = stats.sort_values(
    "Total_PnL",
    ascending=False,
)

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

st.dataframe(
    show,
    use_container_width=True,
)

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

fig_bar.add_vline(
    x=0,
    line_width=2,
    line_color="black",
)

st.plotly_chart(
    fig_bar,
    use_container_width=True,
)

st.divider()


# ====================== 6. ONE-PRODUCT DEEP DIVE ======================
st.subheader("🔍 Deep Dive – One Product")

product_choice = st.selectbox(
    "Select product",
    all_products,
)

one = filtered.loc[
    filtered[col_product] == product_choice
].copy()

if not one.empty:
    one_pnl = float(one["PnL"].sum())
    one_win_rate = float(
        (one["PnL"] > 0).mean() * 100
    )

    deep_dive_columns = st.columns(4)

    deep_dive_columns[0].metric(
        "P&L",
        money_text(one_pnl),
    )

    deep_dive_columns[1].metric(
        "Win Rate",
        f"{one_win_rate:.1f}%",
    )

    deep_dive_columns[2].metric(
        "Trades",
        len(one),
    )

    deep_dive_columns[3].metric(
        "Average per Trade",
        money_text(one_pnl / len(one)),
    )

    one = one.sort_values(col_date)
    one["Cumulative"] = one["PnL"].cumsum()

    fig_one = px.area(
        one,
        x=col_date,
        y="Cumulative",
        title=f"Equity Curve – {product_choice}",
    )

    fig_one.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray",
    )

    st.plotly_chart(
        fig_one,
        use_container_width=True,
    )

st.divider()


# ====================== 7. OVERALL EQUITY CURVE ======================
st.subheader("📈 Overall Equity Curve (Closed Trades Only)")

filtered = filtered.sort_values(col_date).copy()
filtered["Cumulative"] = filtered["PnL"].cumsum()

fig_eq = px.area(
    filtered,
    x=col_date,
    y="Cumulative",
    color_discrete_sequence=["#636EFA"],
)

fig_eq.add_hline(
    y=0,
    line_dash="dash",
    line_color="gray",
)

st.plotly_chart(
    fig_eq,
    use_container_width=True,
)


# ====================== 8. CLOSED TRADES LOG ======================
st.subheader("📋 Closed Trades Log")

log = filtered[
    [col_date, col_product, "PnL"]
].sort_values(
    col_date,
    ascending=False,
)

log.columns = [
    "Date/Time",
    "Product",
    "P&L (SGD)",
]

st.dataframe(
    log,
    use_container_width=True,
    height=300,
)

csv = log.to_csv(index=False).encode("utf-8")

st.download_button(
    "⬇️ Download filtered trades",
    csv,
    "cmc_closed_trades.csv",
    "text/csv",
)

st.info(
    "Account balance, return, gross closed-trade P&L, and costs use the "
    "selected date range. The product selector affects the trade-analysis "
    "sections only."
)