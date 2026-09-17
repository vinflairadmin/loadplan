import io
import pandas as pd
import streamlit as st

# 1. 必須放在所有 Streamlit 指令的最上方
st.set_page_config(page_title="ULD 自動打板與配載系統", layout="wide")


# 2. 解析 Excel 頂部 ULD 航司配額與容量表 (動態邊界)
def parse_uld_quotas_and_cargo(uploaded_file):
  raw_df = pd.read_excel(uploaded_file, header=None)

  # A. 定位貨物標題列
  target_keywords = [
      "customer",
      "kgs",
      "vol",
      "carton",
      "truck",
      "weight",
      "pcs",
  ]
  cargo_header_idx = None
  for idx, row in raw_df.iterrows():
    row_cells = [str(c).lower() for c in row if pd.notna(c)]
    if sum(1 for kw in target_keywords if any(kw in cell for cell in row_cells)) >= 3:
      cargo_header_idx = idx
      break

  if cargo_header_idx is None:
    raise ValueError("⚠️ 找不到貨物資料標題列！請確保包含 Customer, Kgs, Vol 欄位。")

  # B. 定位 CARRIER 配額標題列
  carrier_header_idx = 1
  for idx in range(cargo_header_idx):
    row_cells = [str(c).upper() for c in raw_df.iloc[idx] if pd.notna(c)]
    if any("CARRIER" in c for c in row_cells):
      carrier_header_idx = idx
      break

  # C. 解析 ULD 容量對照表 (Cols 10 & 11)
  vol_mapping = {}
  for idx in range(1, cargo_header_idx):
    uld_name = str(raw_df.iloc[idx, 10]).strip()
    vol_val = raw_df.iloc[idx, 11]
    if pd.notna(uld_name) and pd.notna(vol_val):
      try:
        vol_mapping[uld_name] = float(vol_val)
      except Exception:
        pass

  default_vols = {
      "H3": 2000,
      "H3(N1)": 1600,
      "ZX": 1800,
      "X5-03": 1500,
      "X5-29": 1500,
      "AKH": 400,
      "LD3": 600,
      "BULK": 167,
  }
  for k, v in default_vols.items():
    vol_mapping.setdefault(k, v)

  # D. 動態讀取 CARRIER 配額
  headers = [
      str(c).split("(")[0].strip()
      for c in raw_df.iloc[carrier_header_idx, 0:9].values
  ]
  uld_slots = []

  for r in range(carrier_header_idx + 1, cargo_header_idx):
    carrier = raw_df.iloc[r, 0]
    if pd.notna(carrier) and str(carrier).strip() != "":
      carrier_str = str(carrier).strip()
      for c_idx in range(1, len(headers)):
        cnt = raw_df.iloc[r, c_idx]
        if pd.notna(cnt):
          try:
            count_val = int(float(cnt))
            if count_val > 0:
              uld_type = headers[c_idx]
              max_v = vol_mapping.get(uld_type, 2000)
              for i in range(1, count_val + 1):
                uld_slots.append({
                    "Carrier": carrier_str,
                    "ULD_ID": f"{carrier_str}-{uld_type}-{i}",
                    "ULD_Type": uld_type,
                    "Max_Vol": max_v,
                    "Max_Kg": 1800.0 if uld_type in ["H3", "ZX"] else 1240.0,
                    "Used_Vol": 0.0,
                    "Used_Kg": 0.0,
                    "Items": [],
                })
          except ValueError:
            continue

  # E. 解析貨物資料
  df = raw_df.iloc[cargo_header_idx + 1 :].copy()
  headers_cargo = [
      str(h).strip() if pd.notna(h) else ""
      for h in raw_df.iloc[cargo_header_idx].values
  ]

  seen = {}
  unique_headers = []
  for h in headers_cargo:
    if h in seen:
      seen[h] += 1
      unique_headers.append(f"{h}_{seen[h]}")
    else:
      seen[h] = 0
      unique_headers.append(h)
  df.columns = unique_headers

  col_mapping = {}
  for col in df.columns:
    c_lower = str(col).lower()
    if "customer" in c_lower:
      col_mapping[col] = "Customer"
    elif "truck" in c_lower:
      col_mapping[col] = "Truck"
    elif any(k in c_lower for k in ["carton", "pcs", "ctn"]):
      col_mapping[col] = "No_of_Carton"
    elif any(k in c_lower for k in ["kg", "weight", "gw"]):
      col_mapping[col] = "Kgs"
    elif any(k in c_lower for k in ["vol", "cbm", "measurement"]):
      col_mapping[col] = "Vol"
    elif "carrier" in c_lower:
      col_mapping[col] = "Carrier"
    elif "remark" in c_lower:
      col_mapping[col] = "Remarks"

  df = df.rename(columns=col_mapping)
  if "Kgs" in df.columns:
    df["Kgs"] = pd.to_numeric(df["Kgs"], errors="coerce")
  if "Vol" in df.columns:
    df["Vol"] = pd.to_numeric(df["Vol"], errors="coerce")
  if "No_of_Carton" in df.columns:
    df["No_of_Carton"] = pd.to_numeric(df["No_of_Carton"], errors="coerce")

  cargo_df = df.dropna(subset=["Vol", "Kgs"]).reset_index(drop=True)
  return cargo_df, uld_slots


# 3. 包含二級回流 (Fallback) 機制的配額限制打板演算法
def generate_uld_plan_bounded(cargo_df, uld_slots):
  items = cargo_df.copy()
  items["Vol_Rem"] = items["Vol"]
  items["Kgs_Rem"] = items["Kgs"]

  def classify_cargo(row):
    c = str(row["Carrier"]).strip() if pd.notna(row["Carrier"]) else ""
    r = str(row["Remarks"]).strip() if pd.notna(row["Remarks"]) else ""
    target = "MAIN"
    if "5J" in c:
      target = "5J"
    elif "CX" in c:
      target = "CX"
    elif "走CRK" in r or "CRK" in r:
      target = "CRK"
    is_splittable = not ("PLT" in r or "提貨" in r)
    return pd.Series([target, is_splittable])

  items[["Target_Pool", "Is_Splittable"]] = items.apply(classify_cargo, axis=1)

  pool_ulds = {"5J": [], "CRK": [], "CX": [], "RH": [], "OTHER": []}
  for slot in uld_slots:
    carrier = slot["Carrier"]
    if "5J" in carrier:
      pool_ulds["5J"].append(slot)
    elif "SR" in carrier:
      pool_ulds["CRK"].append(slot)
    elif "CX" in carrier:
      pool_ulds["CX"].append(slot)
    elif "RH" in carrier:
      pool_ulds["RH"].append(slot)
    else:
      pool_ulds["OTHER"].append(slot)

  passes = [
      ("5J", pool_ulds["5J"]),
      ("CX", pool_ulds["CX"]),
      ("CRK", pool_ulds["CRK"]),
      ("MAIN", pool_ulds["CX"] + pool_ulds["RH"] + pool_ulds["OTHER"]),
      ("CRK_FALLBACK", pool_ulds["RH"] + pool_ulds["5J"] + pool_ulds["OTHER"]),
  ]

  for pool_name, available_slots in passes:
    if pool_name == "CRK_FALLBACK":
      pool_items = items[
          (items["Target_Pool"] == "CRK") & (items["Vol_Rem"] > 0)
      ]
    else:
      pool_items = items[items["Target_Pool"] == pool_name]

    if pool_items.empty:
      continue

    for idx, row in pool_items.iterrows():
      rem_vol = items.at[idx, "Vol_Rem"]
      if rem_vol <= 0:
        continue

      for slot in available_slots:
        avail_vol = slot["Max_Vol"] - slot["Used_Vol"]
        if avail_vol <= 0:
          continue

        if row["Is_Splittable"]:
          fit_vol = min(rem_vol, avail_vol)
        else:
          if rem_vol <= avail_vol:
            fit_vol = rem_vol
          else:
            continue

        ratio_kg = (
            (fit_vol / row["Vol"]) * row["Kgs"] if row["Vol"] > 0 else 0.0
        )

        slot["Used_Vol"] += fit_vol
        slot["Used_Kg"] += ratio_kg
        items.at[idx, "Vol_Rem"] -= fit_vol
        items.at[idx, "Kgs_Rem"] -= ratio_kg

        truck_str = f" {row['Truck']}" if pd.notna(row['Truck']) else ""
        c_desc = f"{row['Customer']}{truck_str} ({int(fit_vol)} Vol)"
        slot["Items"].append(c_desc)

        rem_vol = items.at[idx, "Vol_Rem"]
        if rem_vol <= 0:
          break

  plan_rows = []
  for slot in uld_slots:
    used_v = slot["Used_Vol"]
    max_v = slot["Max_Vol"]
    util = round((used_v / max_v) * 100, 1) if max_v > 0 else 0.0
    items_str = (
        " + ".join(slot["Items"]) if slot["Items"] else "預留空板 (Buffer)"
    )

    plan_rows.append({
        "Carrier": slot["Carrier"],
        "ULD 編號": slot["ULD_ID"],
        "ULD 類型": slot["ULD_Type"],
        "裝載體積 (Vol)": f"{int(used_v)} / {int(max_v)}",
        "裝載毛重 (kg)": f"{round(slot['Used_Kg'], 2)} kg",
        "容量利用率": f"{util}%",
        "裝載貨物組合": items_str,
    })

  overflow_items = items[items["Vol_Rem"] > 0]
  if not overflow_items.empty:
    for idx, row in overflow_items.iterrows():
      plan_rows.append({
          "Carrier": "溢出未分配 (Overflow)",
          "ULD 編號": "OVERFLOW",
          "ULD 類型": "N/A",
          "裝載體積 (Vol)": f"{int(row['Vol_Rem'])} Vol",
          "裝載毛重 (kg)": f"{round(row['Kgs_Rem'], 2)} kg",
          "容量利用率": "N/A",
          "裝載貨物組合": (
              f"⚠️ 航司配額全數用盡，無法裝載: {row['Customer']} ({int(row['Vol_Rem'])}"
              " Vol)"
          ),
      })

  return pd.DataFrame(plan_rows)


# 4. 轉為 Excel 二進位檔
def convert_df_to_excel(df):
  output = io.BytesIO()
  with pd.ExcelWriter(output, engine="openpyxl") as writer:
    df.to_excel(writer, index=False, sheet_name="ULD_Load_Plan")
  return output.getvalue()


# 5. UI 畫面渲染
st.title("✈️ 航空貨運 ULD 自動打板系統")

uploaded_file = st.file_uploader("上傳 Excel 檔案 (.xlsx)", type=["xlsx"])

if uploaded_file:
  try:
    cargo_df, uld_slots = parse_uld_quotas_and_cargo(uploaded_file)
    st.success("✅ 成功自動定位並讀取貨物與配額資料！")

    col1, col2, col3, col4 = st.columns(4)
    if "No_of_Carton" in cargo_df.columns:
      col1.metric(
          "總件數 (Cartons)", f"{int(cargo_df['No_of_Carton'].sum()):,} 件"
      )
    col2.metric("總毛重 (Gross Weight)", f"{cargo_df['Kgs'].sum():,.2f} kg")
    col3.metric("總體積 (Total Vol)", f"{int(cargo_df['Vol'].sum()):,} Vol")
    col4.metric("總 ULD 板數配額", f"{len(uld_slots)} 塊/艙")

    st.subheader("📋 讀取貨物清單預覽")
    st.dataframe(cargo_df)

    st.divider()

    st.subheader("📦 最佳 ULD 打板配載方案結果 (ULD Load Plan)")
    result_df = generate_uld_plan_bounded(cargo_df, uld_slots)

    excel_bytes = convert_df_to_excel(result_df)
    st.download_button(
        label="📥 一鍵下載打板結果 Excel (.xlsx)",
        data=excel_bytes,
        file_name="ULD_Load_Plan_Result.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    st.dataframe(result_df)

  except Exception as e:
    st.error(f"解析檔案時出現錯誤：{e}")