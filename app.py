import io
import pandas as pd
import streamlit as st


# 1. 自動定位與讀取 Excel 貨物資料
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
    row_cells = [str(c).lower() for c in row if pd.notna(c)]
    matches = sum(
        1 for kw in target_keywords if any(kw in cell for cell in row_cells)
    )
    if matches >= 3:
      header_row_index = idx
      break

  if header_row_index is None:
    raise ValueError(
        '⚠️ 找不到貨物資料標題列！請確保 Excel 包含 Customer, Kgs, Vol 等欄位。'
    )

  headers = [
      str(h).strip() if pd.notna(h) else ''
      for h in raw_df.iloc[header_row_index].values
  ]

  seen = {}
  unique_headers = []
  for h in headers:
    if h in seen:
      seen[h] += 1
      unique_headers.append(f'{h}_{seen[h]}')
    else:
      seen[h] = 0
      unique_headers.append(h)

  df = raw_df.iloc[header_row_index + 1 :].copy()
  df.columns = unique_headers

  col_mapping = {}
  for col in df.columns:
    c_lower = str(col).lower()
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

  if 'Kgs' in df.columns:
    df['Kgs'] = pd.to_numeric(df['Kgs'], errors='coerce')
  if 'Vol' in df.columns:
    df['Vol'] = pd.to_numeric(df['Vol'], errors='coerce')
  if 'No_of_Carton' in df.columns:
    df['No_of_Carton'] = pd.to_numeric(df['No_of_Carton'], errors='coerce')

  valid_cols = [c for c in ['Kgs', 'Vol'] if c in df.columns]
  if valid_cols:
    df = df.dropna(subset=valid_cols)

  return df.reset_index(drop=True)


# 2. 🆕 真實 ULD 動態打板演算法 (根據上傳數據即時裝載計算)
def generate_uld_plan_dynamic(
    df, target_carrier='CX', uld_type='H3', max_vol=2000, max_kg=1800
):
  items = df.copy()
  items['Vol_Rem'] = items['Vol']
  items['Kgs_Rem'] = items['Kgs']

  plan = []
  uld_count = 1

  while True:
    remaining = items[items['Vol_Rem'] > 0]
    if remaining.empty:
      break

    current_vol = 0
    current_kg = 0
    current_cargo = []

    for idx, row in remaining.iterrows():
      if current_vol >= max_vol:
        break

      vol_avail = max_vol - current_vol
      fit_vol = min(row['Vol_Rem'], vol_avail)

      # 按比例計算分拆後的重量
      ratio_kg = (fit_vol / row['Vol']) * row['Kgs'] if row['Vol'] > 0 else 0

      if fit_vol > 0 and (current_kg + ratio_kg <= max_kg or current_vol == 0):
        current_vol += fit_vol
        current_kg += ratio_kg

        items.at[idx, 'Vol_Rem'] -= fit_vol
        items.at[idx, 'Kgs_Rem'] -= ratio_kg

        truck_str = f" {row['Truck']}" if pd.notna(row['Truck']) else ''
        c_desc = f"{row['Customer']}{truck_str} ({int(fit_vol)} Vol)"
        current_cargo.append(c_desc)

    if current_cargo:
      plan.append({
          'Carrier': target_carrier,
          'ULD 編號': f'{target_carrier}-{uld_type}-{uld_count}',
          'ULD 類型': uld_type,
          '裝載體積 (Vol)': f'{int(current_vol)} / {max_vol}',
          '裝載毛重 (kg)': f'{round(current_kg, 2)} kg',
          '容量利用率': f'{round((current_vol / max_vol) * 100, 1)}%',
          '裝載貨物組合': ' + '.join(current_cargo),
      })
      uld_count += 1

  return pd.DataFrame(plan)


# 3. 轉為 Excel 二進位檔
def convert_df_to_excel(df):
  output = io.BytesIO()
  with pd.ExcelWriter(output, engine='openpyxl') as writer:
    df.to_excel(writer, index=False, sheet_name='ULD_Load_Plan')
  return output.getvalue()


# 4. Streamlit 介面
st.set_page_config(page_title='ULD 自動打板與配載系統', layout='wide')
st.title('✈️ 航空貨運 ULD 自動打板系統')

uploaded_file = st.file_uploader('上傳 Excel 檔案 (.xlsx)', type=['xlsx'])

if uploaded_file:
  try:
    cargo_df = auto_load_cargo_data(uploaded_file)

    st.success('✅ 成功自動定位並讀取貨物資料！')

    col1, col2, col3, col4 = st.columns(4)
    if 'No_of_Carton' in cargo_df.columns:
      col1.metric(
          '總件數 (Cartons)', f"{int(cargo_df['No_of_Carton'].sum()):,} 件"
      )
    col2.metric('總毛重 (Gross Weight)', f"{cargo_df['Kgs'].sum():,.2f} kg")
    col3.metric('總體積 (Total Vol)', f"{int(cargo_df['Vol'].sum()):,} Vol")
    col4.metric('貨物筆數', f'{len(cargo_df)} 筆')

    st.subheader('📋 讀取貨物清單預覽')
    st.dataframe(cargo_df, use_container_width=True)

    st.divider()

    # 打板結果區域 (實時算出來的動態結果)
    st.subheader('📦 最佳 ULD 打板配載方案結果 (ULD Load Plan)')

    # 呼叫動態運算演算法
    result_df = generate_uld_plan_dynamic(cargo_df)

    excel_bytes = convert_df_to_excel(result_df)
    st.download_button(
        label='📥 一鍵下載打板結果 Excel (.xlsx)',
        data=excel_bytes,
        file_name='ULD_Load_Plan_Result.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    st.dataframe(result_df, use_container_width=True)

  except Exception as e:
    st.error(f'解析檔案時出現錯誤：{e}')