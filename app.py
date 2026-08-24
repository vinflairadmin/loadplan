import pandas as pd
import streamlit as st


# 1. 自動定位與讀取 Excel 貨物資料 Function
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
    for idx, row in raw_df.iterrows():
        row_str = row.astype(str).str.lower().tolist()
        matches = sum(
            1 for kw in target_keywords if any(kw in cell for cell in row_str)
        )
        if matches >= 2:
            header_row_index = idx
            break

    if header_row_index is None:
        raise ValueError(
            "⚠️ 找不到貨物資料標題行！請確保 Excel 包含 Customer, Kgs, Vol"
            " 等欄位。"
        )

    headers = raw_df.iloc[header_row_index].values
    df = raw_df.iloc[header_row_index + 1 :].copy()
    df.columns = [str(h).strip() for h in headers]

    col_mapping = {}
    for col in df.columns:
        c_lower = col.lower()
        if 'customer' in c_lower:
            col_mapping[col] = 'Customer'
        elif 'truck' in c_lower:
            col_mapping[col] = 'Truck'
        elif any(k in c_lower for k in ['carton', 'pcs', 'ctn']):
            col_mapping[col] = 'No_of_Carton'
        elif any(k in c_lower for k in ['kg', 'weight', 'gw']):
            col_mapping[col] = 'Kgs'
        elif any(k in c_lower for k in ['vol', 'cbm', 'measurement']):
            col_mapping[col] = 'Vol'
        elif 'remark' in c_lower:
            col_mapping[col] = 'Remarks'

    df = df.rename(columns=col_mapping)
    df['Kgs'] = pd.to_numeric(df['Kgs'], errors='coerce')
    df['Vol'] = pd.to_numeric(df['Vol'], errors='coerce')
    if 'No_of_Carton' in df.columns:
        df['No_of_Carton'] = pd.to_numeric(df['No_of_Carton'], errors='coerce')

    return df.dropna(subset=['Kgs', 'Vol']).reset_index(drop=True)


# 2. Streamlit Web App 介面
st.set_page_config(page_title="ULD 自動打板與配載系統", layout="wide")
st.title("✈️ 航空貨運 ULD 自動打板系統")

uploaded_file = st.file_uploader("上傳 Excel 檔案 (.xlsx)", type=["xlsx"])

if uploaded_file:
    try:
        cargo_df = auto_load_cargo_data(uploaded_file)

        st.success("✅ 成功自動定位並讀取貨物資料！")

        col1, col2, col3, col4 = st.columns(4)
        if 'No_of_Carton' in cargo_df.columns:
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