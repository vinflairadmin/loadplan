import io
import os
import pandas as pd
import streamlit as st

# 1. 頁面基本設定 (必須放在首行)
st.set_page_config(page_title="✈️ 航空貨運 ULD 自動打板系統", layout="wide")


# 2. 自動偵測表格位置與動態解析 (支援配額表在最頂部或最底部，以及有無 ETD 欄位)
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
    if (
        cargo_header_idx is None
        and sum(1 for kw in target_cargo_kw if any(kw in cell for cell in row_cells))
        >= 3
    ):
      cargo_header_idx = idx
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

  if cargo_header_idx < carrier_header_idx:
    cargo_raw = raw_df.iloc[cargo_header_idx + 1 : carrier_header_idx].copy()
    quota_raw = raw_df.iloc[carrier_header_idx:].copy()
  else:
    quota_raw = raw_df.iloc[carrier_header_idx:cargo_header_idx].copy()
    cargo_raw = raw_df.iloc[cargo_header_idx + 1 :].copy()

  q_header_row = [
      str(c).strip() if pd.notna(c) else '' for c in quota_raw.iloc[0].values
  ]

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

  # 自動尋找 ETD 欄位位置（若存在）
  etd_col_idx = None
  for c_i, h_val in enumerate(q_header_row):
    if 'ETD' in h_val.upper():
      etd_col_idx = c_i
      break

  # 動態掃瞄 ULD 配額欄位
  uld_slots = []
  for r_idx in range(1, len(quota_raw)):
    carrier = quota_raw.iloc[r_idx, 0]
    if (
        pd.isna(carrier)
        or str(carrier).strip() == ''
        or str(carrier).strip().upper() == 'NAN'
    ):
      continue

    carrier_str = str(carrier).strip()

    etd_str = ''
    if etd_col_idx is not None and quota_raw.shape[1] > etd_col_idx:
      cell_etd = quota_raw.iloc[r_idx, etd_col_idx]
      if pd.notna(cell_etd):
        etd_str = str(cell_etd).strip()

    # 判斷夜機 (ETD >= 20:00 或 RH 預設夜機)
    is_night = False
    if ':' in etd_str:
      try:
        hour = int(etd_str.split(':')[0])
        if hour >= 20:
          is_night = True
      except Exception:
        pass
    elif (
        'RH' in carrier_str.upper()
        or 'CX913' in carrier_str.upper()
        or 'CX939' in carrier_str.upper()
    ):
      is_night = True

    for c_idx in range(1, len(q_header_row)):
      if c_idx == etd_col_idx:
        continue

      col_name = q_header_row[c_idx]
      if not col_name or col_name.upper() in ['CARRIER', 'ETD', 'VOL', 'NAN']:
        continue

      cnt = quota_raw.iloc[r_idx, c_idx]
      if pd.notna(cnt):
        try:
          count_val = int(float(cnt))
          if count_val > 0:
            clean_type = col_name.split('(')[0].strip()
            max_v = vol_mapping.get(clean_type, 2000.0)
            max_k = kg_mapping.get(clean_type, 1800.0)

            if clean_type.upper() == 'BULK' and count_val > 1:
              uld_slots.append({
                  'Carrier': carrier_str,
                  'ETD': etd_str,
                  'Is_Night': is_night,
                  'ULD_ID': f'{carrier_str}-BULK (共{count_val}艙)',
                  'ULD_Type': 'BULK',
                  'Max_Vol': max_v * count_val,
                  'Max_Kg': max_k * count_val,
                  'Used_Vol': 0.0,
                  'Used_Kg': 0.0,
                  'Items': [],
              })
            else:
              for i in range(1, count_val + 1):
                uld_slots.append({
                    'Carrier': carrier_str,
                    'ETD': etd_str,
                    'Is_Night': is_night,
                    'ULD_ID': f'{carrier_str}-{clean_type}-{i}',
                    'ULD_Type': clean_type,
                    'Max_Vol': max_v,
                    'Max_Kg': max_k,
                    'Used_Vol': 0.0,
                    'Used_Kg': 0.0,
                    'Items': [],
                })
        except ValueError:
          continue

  # 解析貨物資料
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

  cargo_df = cargo_raw.rename(columns=col_mapping)
  
  # 嚴格確保所有數值欄位均為 float 型態，杜絕任何 int64 轉型衝突
  cargo_df['Kgs'] = pd.to_numeric(cargo_df['Kgs'], errors='coerce')
  cargo_df['Vol'] = pd.to_numeric(cargo_df['Vol'], errors='coerce')

  if 'No_of_Carton' in cargo_df.columns:
    cargo_df['No_of_Carton'] = pd.to_numeric(
        cargo_df['No_of_Carton'], errors='coerce'
    ).astype(float)

  cargo_df = cargo_df.dropna(subset=['Vol', 'Kgs']).reset_index(drop=True)
  cargo_df = cargo_df[cargo_df['Vol'] > 0].reset_index(drop=True)

  return cargo_df, uld_slots


# 3. 打板演算法：優先填滿 5J，留 RH 作為夜機 Buffer
def generate_uld_plan_v3(cargo_df, uld_slots):
  items = cargo_df.copy()
  items['Vol_Rem'] = items['Vol']
  items['Kgs_Rem'] = items['Kgs']

  def classify(row):
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
      classify, axis=1
  )

  pool_ulds = {
      '5J': [s for s in uld_slots if '5J' in s['Carrier']],
      'CX': [s for s in uld_slots if 'CX' in s['Carrier']],
      'SR815': [s for s in uld_slots if 'SR' in s['Carrier']],
      'RH': [s for s in uld_slots if 'RH' in s['Carrier']],
      'OTHER': [
          s
          for s in uld_slots
          if not any(k in s['Carrier'] for k in ['5J', 'CX', 'SR', 'RH'])
      ],
  }

  mnl_tiktok_kg = 0.0
  MAX_TIKTOK_MNL_KG = 6000.0

  passes = [
      ('5J_ASSIGNED', pool_ulds['5J'], items[items['Target_Pool'] == '5J']),
      ('CX_ASSIGNED', pool_ulds['CX'], items[items['Target_Pool'] == 'CX']),
      (
          'CRK_TIKTOK',
          pool_ulds['SR815'],
          items[(items['Is_TikTok']) & (items['Target_Pool'] == 'CRK')],
      ),
      (
          'CRK_OTHER',
          pool_ulds['SR815'],
          items[(~items['Is_TikTok']) & (items['Target_Pool'] == 'CRK')],
      ),
      ('DAY_CX', pool_ulds['CX'], items[items['Vol_Rem'] > 0]),
      ('5J_FALLBACK', pool_ulds['5J'], items[items['Vol_Rem'] > 0]),
      ('NIGHT_RH', pool_ulds['RH'], items[items['Vol_Rem'] > 0]),
  ]

  for pass_name, available_slots, pool_items in passes:
    if pool_items.empty:
      continue

    for idx, row in pool_items.iterrows():
      rem_vol = items.at[idx, 'Vol_Rem']
      rem_kg = items.at[idx, 'Kgs_Rem']
      if rem_vol <= 0.001:
        continue

      density = (row['Kgs'] / row['Vol']) if row['Vol'] > 0 else 0.0

      for slot in available_slots:
        avail_vol = slot['Max_Vol'] - slot['Used_Vol']
        avail_kg = slot['Max_Kg'] - slot['Used_Kg']

        if avail_vol <= 0.001 or avail_kg <= 0.001:
          continue

        is_mnl = any(
            k in slot['Carrier'] for k in ['CX', 'RH', '5J']
        ) and ('CRK' not in slot['Carrier'])
        if row['Is_TikTok'] and is_mnl:
          if mnl_tiktok_kg >= MAX_TIKTOK_MNL_KG - 0.001:
            continue
          else:
            avail_kg = min(avail_kg, MAX_TIKTOK_MNL_KG - mnl_tiktok_kg)

        vol_fit_by_kg = (avail_kg / density) if density > 0 else rem_vol
        max_possible_vol = min(rem_vol, avail_vol, vol_fit_by_kg)

        if row['Is_Splittable']:
          fit_vol = max_possible_vol
        else:
          if (
              rem_vol <= avail_vol + 0.001
              and rem_kg <= avail_kg + 0.001
          ):
            fit_vol = rem_vol
          else:
            continue

        if fit_vol <= 0.001:
          continue

        ratio_kg = (
            (fit_vol / row['Vol']) * row['Kgs'] if row['Vol'] > 0 else 0.0
        )

        slot['Used_Vol'] += fit_vol
        slot['Used_Kg'] += ratio_kg
        items.at[idx, 'Vol_Rem'] -= fit_vol
        items.at[idx, 'Kgs_Rem'] -= ratio_kg

        if row['Is_TikTok'] and is_mnl:
          mnl_tiktok_kg += ratio_kg

        truck_str = f" {row['Truck']}" if pd.notna(row['Truck']) else ''
        v_item_disp = (
            f'{int(round(fit_vol))}'
            if abs(fit_vol - round(fit_vol)) < 0.01
            else f'{fit_vol:.2f}'
        )
        c_desc = f"{row['Customer']}{truck_str} ({v_item_disp} Vol)"
        slot['Items'].append(c_desc)

        rem_vol = items.at[idx, 'Vol_Rem']
        if rem_vol <= 0.001:
          break

  plan_rows = []
  for slot in uld_slots:
    used_v = round(slot['Used_Vol'], 2)
    max_v = round(slot['Max_Vol'], 2)
    used_k = round(slot['Used_Kg'], 2)
    max_k = round(slot['Max_Kg'], 2)

    util_v = round((used_v / max_v) * 100, 1) if max_v > 0 else 0.0

    if slot['Items']:
      items_str = ' + '.join(slot['Items'])
    else:
      if slot['Is_Night']:
        items_str = '預留空板 (Buffer - 夜機趕機預留)'
      else:
        items_str = '預留空板 (Buffer)'

    v_disp = (
        f'{int(round(used_v))}'
        if abs(used_v - round(used_v)) < 0.01
        else f'{used_v:.2f}'
    )
    mv_disp = (
        f'{int(round(max_v))}'
        if abs(max_v - round(max_v)) < 0.01
        else f'{max_v:.2f}'
    )
    k_disp = f'{used_k:.2f}'
    mk_disp = (
        f'{int(round(max_k))}'
        if abs(max_k - round(max_k)) < 0.01
        else f'{max_k:.2f}'
    )

    plan_rows.append({
        'Carrier': slot['Carrier'],
        'ETD': slot['ETD'],
        'ULD 編號': slot['ULD_ID'],
        'ULD 類型': slot['ULD_Type'],
        '裝載體積 (Vol)': f'{v_disp} / {mv_disp}',
        '裝載毛重 (kg)': f'{k_disp} / {mk_disp} kg',
        '容量利用率': f'{util_v}%',
        '裝載貨物組合': items_str,
    })

  overflow_items = items[items['Vol_Rem'] > 0.01]
  if not overflow_items.empty:
    for idx, row in overflow_items.iterrows():
      ov_v = round(row['Vol_Rem'], 2)
      ov_k = round(row['Kgs_Rem'], 2)
      v_disp = (
          f'{int(round(ov_v))}'
          if abs(ov_v - round(ov_v)) < 0.01
          else f'{ov_v:.2f}'
      )
      plan_rows.append({
          'Carrier': '溢出未分配 (Overflow)',
          'ETD': 'N/A',
          'ULD 編號': 'OVERFLOW',
          'ULD 類型': 'N/A',
          '裝載體積 (Vol)': f'{v_disp} Vol',
          '裝載毛重 (kg)': f'{ov_k:.2f} kg',
          '容量利用率': 'N/A',
          '裝載貨物組合': (
              '⚠️ 航司配額全數用盡，無法裝載:'
              f" {row['Customer']} ({v_disp} Vol)"
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
      total_cartons = pd.to_numeric(
          cargo_df['No_of_Carton'], errors='coerce'
      ).sum()
      cartons_str = (
          f'{int(round(total_cartons)):,}'
          if abs(total_cartons - round(total_cartons)) < 0.01
          else f'{total_cartons:,.2f}'
      )
      col1.metric('總件數 (Cartons)', f'{cartons_str} 件')
    col2.metric('總毛重 (Gross Weight)', f"{cargo_df['Kgs'].sum():,.2f} kg")
    col3.metric('總體積 (Total Vol)', f"{int(cargo_df['Vol'].sum()):,} Vol")
    col4.metric('總 ULD 板數配額', f'{len(uld_slots)} 塊/艙')

    st.subheader('📋 讀取貨物清單預覽')
    st.dataframe(cargo_df)

    st.divider()

    st.subheader('📦 最佳 ULD 打板配載方案結果 (ULD Load Plan)')
    result_df = generate_uld_plan_v3(cargo_df, uld_slots)

    base_name = os.path.splitext(uploaded_file.name)[0]
    download_filename = f'{base_name}_planned.xlsx'

    excel_bytes = convert_df_to_excel(result_df)
    st.download_button(
        label='📥 一鍵下載打板結果 Excel (.xlsx)',
        data=excel_bytes,
        file_name=download_filename,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    st.dataframe(result_df)

  except Exception as e:
    st.error(f'解析檔案時出現錯誤：{e}')