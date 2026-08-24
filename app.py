import pandas as pd
import streamlit as st


# 1. 容錯版自動讀取與解析 Excel 貨物資料 Function
def auto_load_cargo_data(uploaded_file):
    raw_df = pd.read_excel(uploaded_file, header=None)
    target_keywords = [
        'customer',
        'kgs',
        'vol',
        'carton',
        'truck',
        'weight',
        'pcs',
    ]

    header_row_index = None

    # 搜尋標題列 (Header Row)
    for idx, row in raw_df.iterrows():
        # 強制將非空 cell 轉為小寫字串，避免 float / NaN 報錯
        row_cells = [str(c).lower() for c in row if pd.notna(c)]

        matches = sum(
            1
            for kw in target_keywords
            if any(kw in cell for cell in row_cells)
        )
        if matches >= 2:
            header_row_index = idx
            break

    if header_row_index is None:
        raise ValueError(
            "⚠️ 找不到貨物資料標題列！請確保 Excel 包含 Customer, Kgs, Vol"
            " 等欄位。"
        )

    # 擷取資料並清理 Column 名稱
    headers = [
        str(h).strip() if pd.notna(h) else ""
        for h in raw_df.iloc[header_row_index].values
    ]
    df = raw_df.iloc[header_row_index + 1 :].copy()
    df.columns = headers

    # 欄位自動對應 (Mapping)
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
        elif "remark" in c_lower:
            col_mapping[col] = "Remarks"

    df = df.rename(columns=col_mapping)

    # 轉為數字與過濾無效列
    if "Kgs" in df.columns:
        df["Kgs"] = pd.to_numeric(df["Kgs"], errors="coerce")
    if "Vol" in df.columns:
        df["Vol"] = pd.to_numeric(df["Vol"], errors="coerce")
    if "No_of_Carton" in df.columns:
        df["No_of_Carton"] = pd.to_numeric(df["No_of_Carton"], errors="coerce")

    # 過濾掉沒有數值的雜訊列
    valid_cols = [c for c in ["Kgs", "Vol"] if c in df.columns]
    if valid_cols:
        df = df.dropna(subset=valid_cols)

    return df.reset_index(drop=True)


# 2. Streamlit Web App 介面
st.set_page_config(page_title="ULD 自動打板與配載系統", layout="wide")
st.title("✈️ 航空貨運 ULD 自動打板系統")

uploaded_file = st.file_uploader("上傳 Excel 檔案 (.xlsx)", type=["xlsx"])

if uploaded_file:
    try:
        cargo_df = auto_load_cargo_data(uploaded_file)

        st.success("✅ 成功自動定位並讀取貨物資料！")

        col1, col2, col3, col4 = st.columns(4)
        if "No_of_Carton" in cargo_df.columns:
            col1.metric(
                "總件數 (Cartons)",
                f"{int(cargo_df['No_of_Carton'].sum()):,} 件",
            )
        col2.metric("總毛重 (Gross Weight)", f"{cargo_df['Kgs'].sum():,.2f} kg")
        col3.metric("總體積 (Total Vol)", f"{int(cargo_df['Vol'].sum()):,} Vol")
        col4.metric("貨物筆數", f"{len(cargo_df)} 筆")

        st.subheader("📋 讀取貨物清單預覽")
        st.dataframe(cargo_df, use_container_width=True)

    except Exception as e:
        st.error(f"解析檔案時出現錯誤：{e}")