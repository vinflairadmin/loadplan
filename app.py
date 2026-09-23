import io
import pandas as pd
import streamlit as st

# 1. 頁面基本設定 (必須放在首行)
st.set_page_config(page_title="✈️ 航空貨運 ULD 自動打板系統", layout="wide")


# 2. 自動偵測表格位置與解析 (支援配額表在最頂部或最底部)
def parse_uld_quotas_and_cargo(uploaded_file):
  raw_df = pd.read_excel(uploaded_file, header=None)

  target_cargo_kw = [
      'customer',
      'kgs',
      'vol',
      'carton',
      'truck',
      'weight',
      'pcs',
  ]
  uld_header_kw = ['h3', 'akh', 'zx', 'x5-03', 'ld3', 'bulk']

  cargo_header_idx = None
  carrier_header_idx = None

  for idx, row in raw_df.iterrows():
    row_cells = [str(c).strip().lower() for c in row if pd.notna(c)]

    # 尋找貨物標題列
    if cargo_header_idx is None:
      if sum(1 for kw in target_cargo_kw if any(kw in cell for cell in row_cells)) >= 3:
        cargo_header_idx = idx

    # 尋找航司配額標題列 (識別 CARRIER 或 ULD 欄位)
    if carrier_header_idx is None:
      first_cell = (
          str(row.iloc[0]).strip().lower()
          if len(row) > 0 and pd.notna(row.iloc[0])
          else ''
      )
      if first_cell == 'carrier' or any(u in row_cells for u in uld_header_kw):
        carrier_header_idx = idx

  if cargo_header_idx is None:
    raise ValueError(
        '⚠️ 找不到貨物資料標題列！請確保 Excel 包含 Customer, Kgs, Vol'
        ' 欄位。'
    )

  if carrier_header_idx is None:
    carrier_header_idx = 0 if cargo_header_idx > 0 else cargo_header_idx + 10

  # 判斷配額表在「頂部」還是「底部」
  if cargo_header_idx < carrier_header_idx:
    cargo_raw = raw_df.iloc[cargo_header_idx + 1 : carrier_header_idx].copy()
    quota_raw = raw_df.iloc[carrier_header_idx:].copy()
  else:
    quota_raw = raw_df.iloc[carrier_header_idx:cargo_header_idx].copy()
    cargo_raw = raw_df.iloc[cargo_header_idx + 1 :].copy()

  # A. 解析航司 ULD 板數配額與容量
  q_header = [str(c).strip() for c in quota_raw.iloc[0].values]

  vol_mapping = {
      'H3': 2000.0,
      'H3(N1)': 1600.0,
      'ZX': 1800.0,
      'X5-03': 1500.0,
      'X5-29': 1500.0,
      'AKH': 400.0,
      'LD3': 600.0,
      'BULK': 167.0,
  }
  kg_mapping = {
      'H3': 1800.0,
      'H3(N1)': 1600.0,
      'ZX': 1800.0,
      'X5-03': 1500.0,
      'X5-29': 1240.0,
      'AKH': 1200.0,
      'LD3': 1200.0,
      'BULK': 500.0,
  }

  # 動態讀取自訂 VOL 欄位 (Cols 10 & 11)
  for idx in range(1, len(quota_raw)):
    u_name = (
        str(quota_raw.iloc[idx, 10]).strip()
        if quota_raw.shape[1] > 10 and pd.notna(quota_raw.iloc[idx, 10])
        else ''
    )
    v_val = (
        quota_raw.iloc[idx, 11]
        if quota_raw.shape[1] > 11 and pd.notna(quota_raw.iloc[idx, 11])
        else None
    )
    if u_name and v_val:
      try:
        vol_mapping[u_name] = float(v_val)
      except Exception:
        pass

  uld_slots = []
  for r_idx in range(1, len(quota_raw)):
    carrier = quota_raw.iloc[r_idx, 0]
    if (
        pd.notna(carrier)
        and str(carrier).strip() != ''
        and str(carrier).strip().upper() != 'NAN'
    ):
      carrier_str = str(carrier).strip()
      for c_idx in range(2, min(10, len(q_header))):
        cnt = quota_raw.iloc[r_idx, c_idx]
        if pd.notna(cnt):
          try:
            count_val = int(float(cnt))
            if count_val > 0:
              uld_type = q_header[c_idx]
              clean_uld_type = uld_type.split('(')[0].strip()
              max_v = vol_mapping.get(
                  clean_uld_type, vol_mapping.get(uld_type, 2000.0)
              )
              max_k = kg_mapping.get(
                  clean_uld_type, kg_mapping.get(uld_type, 1800.0)
              )

              for i in range(1, count_val + 1):
                uld_slots.append({
                    'Carrier': carrier_str,
                    'ULD_ID': f'{carrier_str}-{clean_uld_type}-{i}',
                    'ULD_Type': clean_uld_type,
                    'Max_Vol': max_v,
                    'Max_Kg': max_k,
                    'Used_Vol': 0.0,
                    'Used_Kg': 0.0,
                    'Items': [],
                })
          except ValueError:
            continue

  # B. 解析貨物資料 (Cargo Table)
  cargo_headers = [
      str(h).strip() if pd.notna(h) else ''
      for h in raw_df.iloc[cargo_header_idx].values
  ]
  seen = {}
  unique_headers = []
  for h in cargo_headers:
    if h in seen:
      seen[h] += 1
      unique_headers.append(f'{h}_{seen[h]}')
    else:
      seen[h] = 0
      unique_headers.append(h)
  cargo_raw.columns = unique_headers

  col_mapping = {}
  for col in cargo_raw.columns:
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
    elif 'carrier' in c_lower:
      col_mapping[col] = 'Carrier'
    elif 'remark' in c_lower:
      col_mapping[col] = 'Remarks'

  df = cargo_raw.rename(columns=col_mapping)
  df['Kgs'] = pd.to_numeric(df['Kgs'], errors='coerce')
  df['Vol'] = pd.to_numeric(df['Vol'], errors='coerce')
  cargo_df = df.dropna(subset=['Vol', 'Kgs']).reset_index(drop=True)
  cargo_df = cargo_df[cargo_df['Vol'] > 0].reset_index(drop=True)

  return cargo_df, uld_slots


# 3. 升級版雙重限制 (體積+重量) 打板演算法
def generate_uld_plan_enhanced(cargo_df, uld_slots):
  items = cargo_df.copy()
  items['Vol_Rem'] = items['Vol']
  items['Kgs_Rem'] = items['Kgs']

  def classify_cargo(row):
    c = str(row['Carrier']).strip() if pd.notna(row['Carrier']) else ''
    r = str(row['Remarks']).strip() if pd.notna(row['Remarks']) else ''
    cust = str(row['Customer']).strip() if pd.notna(row['Customer']) else ''

    target = 'MAIN'
    if '5J' in c:
      target = '5J'
    elif 'CX' in c:
      target = 'CX'
    elif '走CRK' in r or 'CRK' in r:
      target = 'CRK'

    is_splittable = not ('PLT' in r or '提貨' in r or '唔走得' in r)
    is_tiktok = 'TIKTOK' in cust.upper()

    return pd.Series([target, is_splittable, is_tiktok])

  items[['Target_Pool', 'Is_Splittable', 'Is_TikTok']] = items.apply(
      classify_cargo, axis=1
  )

  pool_ulds = {'5J': [], 'CRK': [], 'CX': [], 'RH': [], 'OTHER': []}
  for slot in uld_slots:
    carrier = slot['Carrier']
    if '5J' in carrier:
      pool_ulds['5J'].append(slot)
    elif 'SR' in carrier or 'CRK' in carrier:
      pool_ulds['CRK'].append(slot)
    elif 'CX' in carrier:
      pool_ulds['CX'].append(slot)
    elif 'RH' in carrier:
      pool_ulds['RH'].append(slot)
    else:
      pool_ulds['OTHER'].append(slot)

  mnl_tiktok_kg = 0.0
  MAX_TIKTOK_MNL_KG = 6000.0

  passes = [
      ('5J', pool_ulds['5J']),
      ('CX', pool_ulds['CX']),
      ('CRK', pool_ulds['CRK']),
      ('MAIN', pool_ulds['CX'] + pool_ulds['RH'] + pool_ulds['OTHER']),
      ('CRK_FALLBACK', pool_ulds['RH'] + pool_ulds['5J'] + pool_ulds['OTHER']),
  ]

  for pool_name, available_slots in passes:
    if pool_name == 'CRK_FALLBACK':
      pool_items = items[
          (items['Target_Pool'] == 'CRK') & (items['Vol_Rem'] > 0)
      ]
    else:
      pool_items = items[items['Target_Pool'] == pool_name]

    if pool_items.empty:
      continue

    for idx, row in pool_items.iterrows():
      rem_vol = items.at[idx, 'Vol_Rem']
      rem_kg = items.at[idx, 'Kgs_Rem']
      if rem_vol <= 0:
        continue

      density = (row['Kgs'] / row['Vol']) if row['Vol'] > 0 else 0.0

      for slot in available_slots:
        avail_vol = slot['Max_Vol'] - slot['Used_Vol']
        avail_kg = slot['Max_Kg'] - slot['Used_Kg']

        if avail_vol <= 0 or avail_kg <= 0:
          continue

        # 檢查 TIKTOK MNL 6,000 kg 限重條款
        is_mnl_flight = any(
            k in slot['Carrier'] for k in ['CX', 'RH', '5J']
        ) and ('CRK' not in slot['Carrier'])
        if row['Is_TikTok'] and is_mnl_flight:
          if mnl_tiktok_kg >= MAX_TIKTOK_MNL_KG:
            continue
          else:
            allowed_tiktok_kg = MAX_TIKTOK_MNL_KG - mnl_tiktok_kg
            avail_kg = min(avail_kg, allowed_tiktok_kg)

        # 計算受體積與重量雙重限制之最大可行裝載量
        vol_fit_by_kg = (avail_kg / density) if density > 0 else rem_vol
        max_possible_vol = min(rem_vol, avail_vol, vol_fit_by_kg)

        if row['Is_Splittable']:
          fit_vol = max_possible_vol
        else:
          if rem_vol <= avail_vol and rem_kg <= avail_kg:
            fit_vol = rem_vol
          else:
            continue

        if fit_vol <= 0:
          continue

        ratio_kg = (
            (fit_vol / row['Vol']) * row['Kgs'] if row['Vol'] > 0 else 0.0
        )

        # 鎖定扣減
        slot['Used_Vol'] += fit_vol
        slot['Used_Kg'] += ratio_kg
        items.at[idx, 'Vol_Rem'] -= fit_vol
        items.at[idx, 'Kgs_Rem'] -= ratio_kg

        if row['Is_TikTok'] and is_mnl_flight:
          mnl_tiktok_kg += ratio_kg

        truck_str = f" {row['Truck']}" if pd.notna(row['Truck']) else ''
        c_desc = f"{row['Customer']}{truck_str} ({int(fit_vol)} Vol)"
        slot['Items'].append(c_desc)

        rem_vol = items.at[idx, 'Vol_Rem']
        if rem_vol <= 0:
          break

  # 格式化輸出結果
  plan_rows = []
  for slot in uld_slots:
    used_v = slot['Used_Vol']
    max_v = slot['Max_Vol']
    used_k = slot['Used_Kg']
    max_k = slot['Max_Kg']

    util_v = round((used_v / max_v) * 100, 1) if max_v > 0 else 0.0
    items_str = (
        ' + '.join(slot['Items']) if slot['Items'] else '預留空板 (Buffer)'
    )

    plan_rows.append({
        'Carrier': slot['Carrier'],
        'ULD 編號': slot['ULD_ID'],
        'ULD 類型': slot['ULD_Type'],
        '裝載體積 (Vol)': f'{int(used_v)} / {int(max_v)}',
        '裝載毛重 (kg)': f'{round(used_k, 2)} / {int(max_k)} kg',
        '容量利用率': f'{util_v}%',
        '裝載貨物組合': items_str,
    })

  overflow_items = items[items['Vol_Rem'] > 0]
  if not overflow_items.empty:
    for idx, row in overflow_items.iterrows():
      plan_rows.append({
          'Carrier': '溢出未分配 (Overflow)',
          'ULD 編號': 'OVERFLOW',
          'ULD 類型': 'N/A',
          '裝載體積 (Vol)': f"{int(row['Vol_Rem'])} Vol",
          '裝載毛重 (kg)': f"{round(row['Kgs_Rem'], 2)} kg",
          '容量利用率': 'N/A',
          '裝載貨物組合': (
              '⚠️ 航司配額全數用盡/單板重量上限限制，無法裝載:'
              f" {row['Customer']} ({int(row['Vol_Rem'])} Vol)"
          ),
      })

  return pd.DataFrame(plan_rows)


# 4. 轉為 Excel 二進位檔
def convert_df_to_excel(df):
  output = io.BytesIO()
  with pd.ExcelWriter(output, engine='openpyxl') as writer:
    df.to_excel(writer, index=False, sheet_name='ULD_Load_Plan')
  return output.getvalue()


# 5. UI 介面
st.title('✈️ 航空貨運 ULD 自動打板系統')

uploaded_file = st.file_uploader('上傳 Excel 檔案 (.xlsx)', type=['xlsx'])

if uploaded_file:
  try:
    cargo_df, uld_slots = parse_uld_quotas_and_cargo(uploaded_file)
    st.success('✅ 成功自動解析配額與貨物資料！')

    col1, col2, col3, col4 = st.columns(4)
    if 'No_of_Carton' in cargo_df.columns:
      col1.metric(
          '總件數 (Cartons)', f"{int(cargo_df['No_of_Carton'].sum()):,} 件"
      )
    col2.metric('總毛重 (Gross Weight)', f"{cargo_df['Kgs'].sum():,.2f} kg")
    col3.metric('總體積 (Total Vol)', f"{int(cargo_df['Vol'].sum()):,} Vol")
    col4.metric('總 ULD 板數配額', f'{len(uld_slots)} 塊/艙')

    st.subheader('📋 讀取貨物清單預覽')
    st.dataframe(cargo_df)

    st.divider()

    st.subheader('📦 最佳 ULD 打板配載方案結果 (ULD Load Plan)')
    result_df = generate_uld_plan_enhanced(cargo_df, uld_slots)

    excel_bytes = convert_df_to_excel(result_df)
    st.download_button(
        label='📥 一鍵下載打板結果 Excel (.xlsx)',
        data=excel_bytes,
        file_name='ULD_Load_Plan_Result.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    st.dataframe(result_df)

  except Exception as e:
    st.error(f'解析檔案時出現錯誤：{e}')