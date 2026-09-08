import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="CMC Multi-Product Tracker", page_icon="📈", layout="wide")

st.title("📈 CMC Markets – Multi-Product Performance Tracker")
st.caption("Clean layout • Current Balance + Profit first • Then Trading Costs")
st.markdown("---")

uploaded_file = st.sidebar.file_uploader("📂 Upload your CMC CSV file", type=["csv", "xlsx"])

if uploaded_file is None:
    st.info("👈 Upload your CMC Markets history CSV to begin")
    st.stop()

# ====================== HELPERS ======================
def clean_money(series):
    if series is None or series.empty:
        return pd.Series(dtype=float)
    s = series.astype(str).str.strip()
    for char in [",", " ", "$", "SGD", "£", "€", "USD", "s$"]:
        s = s.str.replace(char, "", regex=False)
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def find_col(df, possible_names):
    for name in possible_names:
        if name in df.columns:
            return name
    lower_map = {str(c).lower().strip(): c for c in df.columns}
    for name in possible_names:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None

# ====================== READ FILE ======================
try:
    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"Could not read file: {e}")
    st.stop()

# Column detection
col_date          = find_col(df, ["DATE/TIME", "Date/Time", "Date"])
col_type          = find_col(df, ["TYPE", "Type"])
col_product       = find_col(df, ["PRODUCT", "Product", "Instrument", "Symbol"])
col_amount        = find_col(df, ["AMOUNT (SGD)", "Amount (SGD)", "Amount"])
col_balance       = find_col(df, ["BALANCE (SGD)", "Balance (SGD)", "Balance"])
col_holding       = find_col(df, ["HOLDING COST (SGD)", "Holding Cost (SGD)", "HOLDING COST - AMOUNT"])
col_holding_total = find_col(df, ["HOLDING COST - TOTAL (SGD)", "HOLDING COST - TOTAL"])

if not all([col_date, col_type, col_product, col_amount]):
    st.error("Missing critical columns.")
    st.write(list(df.columns))
    st.stop()

# Clean money columns
df[col_amount] = clean_money(df[col_amount])
if col_balance:       df[col_balance] = clean_money(df[col_balance])
if col_holding:       df[col_holding] = clean_money(df[col_holding])
if col_holding_total: df[col_holding_total] = clean_money(df[col_holding_total])

df[col_date] = pd.to_datetime(df[col_date], errors="coerce", dayfirst=True)
df = df.dropna(subset=[col_date]).sort_values(col_date).reset_index(drop=True)

df[col_product] = (
    df[col_product].fillna("").astype(str).str.strip().str.upper()
    .str.replace(r"\s+", " ", regex=True)
    .str.replace(" / ", "/", regex=False)
)

df[col_type] = df[col_type].astype(str).str.strip()
type_lower = df[col_type].str.lower()

# ====================== SIDEBAR FILTERS ======================
st.sidebar.header("🎛️ Filters")
min_d = df[col_date].min().date()
max_d = df[col_date].max().date()
date_range = st.sidebar.date_input("📅 Date Range", value=(min_d, max_d), min_value=min_d, max_value=max_d)

date_mask = (df[col_date].dt.date >= date_range[0]) & (df[col_date].dt.date <= date_range[1])
df_period = df[date_mask].copy()
type_period = type_lower[date_mask]

# ====================== CALCULATIONS ======================
# Current Balance
current_balance = df[col_balance].iloc[-1] if col_balance and df[col_balance].notna().any() else df[col_amount].sum()

# Closed Trades P&L (real profit)
is_close = type_lower.str.contains("close trade", na=False)
has_product = df[col_product].str.len() > 0
trades = df[is_close & has_product].copy()
trades["PnL"] = trades[col_amount]
trades = trades[trades["PnL"] != 0]

mask_trades = (
    (trades[col_date].dt.date >= date_range[0]) &
    (trades[col_date].dt.date <= date_range[1])
)
filtered = trades[mask_trades].copy()
total_pnl = filtered["PnL"].sum() if len(filtered) > 0 else 0.0

# Net Deposits (for % profit calculation)
deposit_mask = type_period.str.contains("payment in|deposit", na=False)
net_deposits = df_period.loc[deposit_mask, col_amount].sum()

# % Profit
pct_profit = ((current_balance - net_deposits) / abs(net_deposits) * 100) if net_deposits != 0 else 0.0

# ========== 1. ACCOUNT SUMMARY (as you requested) ==========
st.subheader("💰 Account Summary")

s1, s2, s3 = st.columns(3)
s1.metric("🏦 Current Balance", f"SGD {current_balance:,.2f}")
s2.metric("📈 Profit (Trading P&L)", f"SGD {total_pnl:,.2f}")
s3.metric("% Profit", f"{pct_profit:+.2f}%", delta="Increase" if pct_profit >= 0 else "Decrease")

st.caption(f"Net Deposits in selected period: SGD {net_deposits:,.2f}")
st.markdown("---")

# ========== 2. TRADING COSTS (second section) ==========
st.subheader("💸 Trading Costs (Commission + Holding Cost)")

# Commission
is_commission = type_period.str.contains("commission charge", na=False)
commission_total = df_period.loc[is_commission, col_amount].sum()

# Holding Cost (fixed to read the correct column)
is_holding = type_period.str.contains("holding cost", na=False)
holding_total = 0.0

if col_holding:
    holding_total += df_period.loc[is_holding, col_holding].sum()
if col_holding_total:
    holding_total += df_period.loc[is_holding, col_holding_total].sum()

# Fallback if still zero
if abs(holding_total) < 0.0001:
    holding_total = df_period.loc[is_holding, col_amount].sum()

if holding_total > 0:
    holding_total = -holding_total

total_costs = commission_total + holding_total

c1, c2, c3 = st.columns(3)
c1.metric("💳 Commission Charges", f"SGD {commission_total:,.2f}")
c2.metric(" Overnight Holding Costs", f"SGD {holding_total:,.2f}")
c3.metric("📉 Total Costs Paid", f"SGD {total_costs:,.2f}")

with st.expander("🔍 See actual cost rows"):
    cost_rows = df_period[is_commission | is_holding].copy()
    if len(cost_rows) > 0:
        cost_rows["Cost"] = 0.0
        cost_rows.loc[is_commission, "Cost"] = cost_rows.loc[is_commission, col_amount]
        if col_holding:
            cost_rows.loc[is_holding, "Cost"] = cost_rows.loc[is_holding, col_holding]
        # final fallback
        still_zero = (cost_rows["Cost"] == 0) & is_holding
        cost_rows.loc[still_zero, "Cost"] = cost_rows.loc[still_zero, col_amount]
        
        st.dataframe(
            cost_rows[[col_date, col_type, col_product, "Cost"]].sort_values(col_date, ascending=False),
            use_container_width=True
        )
    else:
        st.write("No cost rows found in this period.")

st.markdown("---")

# ========== REST OF THE APP ==========
if len(filtered) == 0:
    st.warning("No closed trades found in the selected period.")
    st.stop()

all_products = sorted(filtered[col_product].unique().tolist())
selected_products = st.sidebar.multiselect("🌐 Products to include", all_products, default=all_products)

filtered = filtered[filtered[col_product].isin(selected_products)]

# Trading KPIs
n_trades = len(filtered)
wins = (filtered["PnL"] > 0).sum()
losses = (filtered["PnL"] < 0).sum()
win_rate = (wins / n_trades * 100) if n_trades > 0 else 0
avg_win = filtered.loc[filtered["PnL"] > 0, "PnL"].mean() if wins > 0 else 0
avg_loss = filtered.loc[filtered["PnL"] < 0, "PnL"].mean() if losses > 0 else 0

st.subheader("📊 Trading Performance Summary")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Closed Trades", n_trades)
k2.metric("Win Rate", f"{win_rate:.1f}%", f"{wins}W / {losses}L")
k3.metric("Average Win", f"SGD {avg_win:,.2f}")
k4.metric("Average Loss", f"SGD {avg_loss:,.2f}")

st.divider()

# Product Ranking
st.subheader("🏆 Which Product Makes (or Loses) the Most Money?")
stats = (
    filtered.groupby(col_product)
    .agg(Total_PnL=("PnL", "sum"), Trades=("PnL", "count"),
         Wins=("PnL", lambda x: (x > 0).sum()), Losses=("PnL", lambda x: (x < 0).sum()),
         Avg_PnL=("PnL", "mean"), Best=("PnL", "max"), Worst=("PnL", "min"))
    .reset_index()
)
stats["Win_Rate_%"] = (stats["Wins"] / stats["Trades"] * 100).round(1)
stats = stats.sort_values("Total_PnL", ascending=False)

show = stats[[col_product, "Total_PnL", "Trades", "Win_Rate_%", "Avg_PnL", "Best", "Worst"]].copy()
show.columns = ["Product", "Total P&L (SGD)", "Trades", "Win Rate %", "Avg P&L", "Best Trade", "Worst Trade"]
for col in ["Total P&L (SGD)", "Avg P&L", "Best Trade", "Worst Trade"]:
    show[col] = show[col].apply(lambda x: f"{x:,.2f}")
st.dataframe(show, use_container_width=True)

# Bar chart
fig_bar = go.Figure(go.Bar(
    y=stats[col_product], x=stats["Total_PnL"], orientation="h",
    marker_color=["#00CC96" if x >= 0 else "#EF553B" for x in stats["Total_PnL"]],
    text=stats["Total_PnL"].apply(lambda x: f"{x:,.2f}"), textposition="outside"
))
fig_bar.update_layout(height=max(400, len(stats)*45), yaxis={"categoryorder": "total ascending"})
fig_bar.add_vline(x=0, line_width=2, line_color="black")
st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# Deep Dive
st.subheader("🔍 Deep Dive – One Product")
product_choice = st.selectbox("Select product", all_products)
one = filtered[filtered[col_product] == product_choice].copy()

if len(one) > 0:
    one_pnl = one["PnL"].sum()
    one_wr = (one["PnL"] > 0).mean() * 100
    
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("P&L", f"SGD {one_pnl:,.2f}")
    d2.metric("Win Rate", f"{one_wr:.1f}%")
    d3.metric("Trades", len(one))
    d4.metric("Avg per Trade", f"SGD {one_pnl/len(one):,.2f}")
    
    one = one.sort_values(col_date)
    one["Cumulative"] = one["PnL"].cumsum()
    fig = px.area(one, x=col_date, y="Cumulative", title=f"Equity Curve – {product_choice}")
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# Overall Equity Curve
st.subheader("📈 Overall Equity Curve (Closed Trades only)")
filtered = filtered.sort_values(col_date)
filtered["Cumulative"] = filtered["PnL"].cumsum()
fig_eq = px.area(filtered, x=col_date, y="Cumulative", color_discrete_sequence=["#636EFA"])
fig_eq.add_hline(y=0, line_dash="dash", line_color="gray")
st.plotly_chart(fig_eq, use_container_width=True)

# Log
st.subheader("📋 Closed Trades Log")
log = filtered[[col_date, col_product, "PnL"]].sort_values(col_date, ascending=False)
log.columns = ["Date/Time", "Product", "P&L (SGD)"]
st.dataframe(log, use_container_width=True, height=300)

csv = log.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Download filtered trades", csv, "cmc_closed_trades.csv", "text/csv")

st.success("✅ Layout updated: Account Summary (Balance + Profit + % Profit) → Trading Costs")