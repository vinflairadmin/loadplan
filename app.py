import io
import os
import re
import pandas as pd
import streamlit as st

# 1. 頁面基本設定
st.set_page_config(page_title="✈️ 航空貨運 ULD 自動打板系統 V6", layout="wide")

# 2. 自動偵測表格位置與解析
def parse_uld_quotas_and_cargo(uploaded_file):
    raw_df = pd.read_excel(uploaded_file, header=None)
    
    target_cargo_kw = ['customer', 'kgs', 'vol', 'carton', 'truck', 'weight', 'pcs']
    uld_header_kw = ['h3', 'akh', 'zx', 'x5-03', 'ld3', 'bulk']
    
    cargo_header_idx = None
    carrier_header_idx = None
    
    for idx, row in raw_df.iterrows():
        row_cells = [str(c).strip().lower() for c in row if pd.notna(c)]
        if cargo_header_idx is None and sum(1 for kw in target_cargo_kw if any(kw in cell for cell in row_cells)) >= 3:
            cargo_header_idx = idx
        first_cell = str(row.iloc[0]).strip().lower() if len(row) > 0 and pd.notna(row.iloc[0]) else ''
        if carrier_header_idx is None and (first_cell == 'carrier' or any(u in row_cells for u in uld_header_kw)):
            carrier_header_idx = idx

    if cargo_header_idx is None:
        raise ValueError('⚠️ 找不到貨物資料標題列！請確保 Excel 包含 Customer, Kgs, Vol 欄位。')
    if carrier_header_idx is None:
        carrier_header_idx = 0 if cargo_header_idx > 0 else cargo_header_idx + 10

    # 切割 DataFrame
    if cargo_header_idx < carrier_header_idx:
        cargo_raw = raw_df.iloc[cargo_header_idx+1:carrier_header_idx].copy()
        quota_raw = raw_df.iloc[carrier_header_idx:].copy()
    else:
        quota_raw = raw_df.iloc[carrier_header_idx:cargo_header_idx].copy()
        cargo_raw = raw_df.iloc[cargo_header_idx+1:].copy()

    q_header = [str(c).strip() for c in quota_raw.iloc[0].values]
    
    vol_mapping = {'H3': 2000.0, 'H3(N1)': 1600.0, 'ZX': 1800.0, 'X5-03': 1500.0, 'X5-29': 1500.0, 'AKH': 400.0, 'LD3': 600.0, 'BULK': 167.0}
    kg_mapping = {'H3': 1800.0, 'H3(N1)': 1600.0, 'ZX': 1800.0, 'X5-03': 1500.0, 'X5-29': 1240.0, 'AKH': 1200.0, 'LD3': 1200.0, 'BULK': 500.0}

    # 動態自訂 VOL 欄位讀取
    try:
        vol_col_indices = [i for i, h in enumerate(q_header) if h.upper() == 'VOL']
        if vol_col_indices:
            vol_col_idx = vol_col_indices[0]
            for idx in range(1, len(quota_raw)):
                u_name = str(quota_raw.iloc[idx, vol_col_idx-1]).strip()
                v_val = quota_raw.iloc[idx, vol_col_idx]
                if pd.notna(u_name) and u_name and pd.notna(v_val):
                    try:
                        vol_mapping[u_name] = float(v_val)
                    except Exception:
                        pass
    except Exception:
        pass

    uld_slots = []
    etd_col_idx = None
    for c_idx, h in enumerate(q_header):
        if h.upper() == 'ETD':
            etd_col_idx = c_idx
            break

    for r_idx in range(1, len(quota_raw)):
        carrier = quota_raw.iloc[r_idx, 0]
        if pd.isna(carrier) or str(carrier).strip() == '' or str(carrier).strip().upper() in ['NAN', 'CARRIER']:
            continue
            
        carrier_str = str(carrier).strip()
        
        etd_str = ''
        if etd_col_idx is not None:
            etd_val = quota_raw.iloc[r_idx, etd_col_idx]
            etd_str = str(etd_val).strip() if pd.notna(etd_val) and str(etd_val).strip().upper() != 'NAN' else ''
            
        is_night = False
        if ':' in etd_str:
            try:
                hour = int(etd_str.split(':')[0])
                if hour >= 20:
                    is_night = True
            except Exception:
                pass
        
        if not etd_str and 'RH' in carrier_str.upper():
            is_night = True

        for c_idx in range(1, len(q_header)):
            header_name = q_header[c_idx].upper()
            if header_name in ['ETD', 'NAN', 'VOL', 'REMARKS'] or header_name == '':
                continue
                
            cnt = quota_raw.iloc[r_idx, c_idx]
            if pd.notna(cnt):
                count_val = 0
                try:
                    count_val = int(float(cnt))
                except Exception:
                    match = re.search(r'\d+', str(cnt))
                    if match:
                        count_val = int(match.group())
                
                if count_val > 0:
                    uld_type = q_header[c_idx]
                    clean_type = uld_type.split('(')[0].strip()
                    max_v = vol_mapping.get(clean_type, 2000.0)
                    max_k = kg_mapping.get(clean_type, 1800.0)
                    
                    # 【核心修改】將每個艙位拆分為獨立 ULD，不要合併，符合原版邏輯
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
                            'Items': []
                        })

    cargo_headers = [str(h).strip() if pd.notna(h) else '' for h in raw_df.iloc[cargo_header_idx].values]
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
        if 'customer' in c_lower: col_mapping[col] = 'Customer'
        elif 'truck' in c_lower: col_mapping[col] = 'Truck'
        elif any(k in c_lower for k in ['carton', 'pcs', 'ctn']): col_mapping[col] = 'No_of_Carton'
        elif any(k in c_lower for k in ['kg', 'weight', 'gw']): col_mapping[col] = 'Kgs'
        elif any(k in c_lower for k in ['vol', 'cbm', 'measurement']): col_mapping[col] = 'Vol'
        elif 'carrier' in c_lower: col_mapping[col] = 'Carrier'
        elif 'remark' in c_lower: col_mapping[col] = 'Remarks'

    cargo_df = cargo_raw.rename(columns=col_mapping)
    cargo_df['Kgs'] = cargo_df['Kgs'].astype(str).str.replace(',', '', regex=False)
    cargo_df['Vol'] = cargo_df['Vol'].astype(str).str.replace(',', '', regex=False)
    
    cargo_df['Kgs'] = pd.to_numeric(cargo_df['Kgs'], errors='coerce').astype(float)
    cargo_df['Vol'] = pd.to_numeric(cargo_df['Vol'], errors='coerce').astype(float)
    
    if 'No_of_Carton' in cargo_df.columns:
        cargo_df['No_of_Carton'] = pd.to_numeric(cargo_df['No_of_Carton'], errors='coerce').astype(float)

    cargo_df = cargo_df.dropna(subset=['Vol', 'Kgs']).reset_index(drop=True)
    cargo_df = cargo_df[cargo_df['Vol'] > 0].reset_index(drop=True)
    
    return cargo_df, uld_slots

# 3. 打板演算法
def generate_uld_plan_v6(cargo_df, uld_slots):
    items = cargo_df.copy()
    items['Vol_Rem'] = items['Vol']
    items['Kgs_Rem'] = items['Kgs']

    def classify(row):
        c = str(row['Carrier']).strip().upper() if pd.notna(row['Carrier']) else ''
        r = str(row['Remarks']).strip().upper() if pd.notna(row['Remarks']) else ''
        cust = str(row['Customer']).strip().upper() if pd.notna(row['Customer']) else ''

        target = 'MAIN'
        if 'CRK' in c or '走CRK' in r or 'CRK' in r:
            target = 'CRK'
        elif '5J' in c:
            target = '5J'
        elif 'CX' in c:
            target = 'CX'

        is_tiktok = 'TIKTOK' in cust
        return pd.Series([target, is_tiktok])

    items[['Target_Pool', 'Is_TikTok']] = items.apply(classify, axis=1)

    mnl_tiktok_kg = 0.0
    MAX_TIKTOK_MNL_KG = 6000.0

    slots_5j = [s for s in uld_slots if '5J' in s['Carrier'].upper() and 'CRK' not in s['Carrier'].upper()]
    slots_cx = [s for s in uld_slots if 'CX' in s['Carrier'].upper() and 'CRK' not in s['Carrier'].upper()]
    slots_crk = [s for s in uld_slots if 'CRK' in s['Carrier'].upper() or 'SR' in s['Carrier'].upper()]
    
    slots_morning = [s for s in uld_slots if not s['Is_Night'] and 'RH' not in s['Carrier'].upper()]
    slots_night_other = [s for s in uld_slots if s['Is_Night'] and 'RH' not in s['Carrier'].upper()]
    slots_rh = [s for s in uld_slots if 'RH' in s['Carrier'].upper()]

    passes = [
        ('5J_ASSIGNED', slots_5j, items[items['Target_Pool'] == '5J']),
        ('CX_ASSIGNED', slots_cx, items[items['Target_Pool'] == 'CX']),
        ('CRK_TIKTOK', slots_crk, items[(items['Is_TikTok']) & (items['Target_Pool'] == 'CRK')]),
        ('CRK_OTHER', slots_crk, items[(~items['Is_TikTok']) & (items['Target_Pool'] == 'CRK')]),
        ('MORNING_SWEEP', slots_morning, items[items['Vol_Rem'] > 0]),
        ('NIGHT_OTHER_SWEEP', slots_night_other, items[items['Vol_Rem'] > 0]),
        ('RH_SWEEP', slots_rh, items[items['Vol_Rem'] > 0]),
        ('FORCE_SWEEP', uld_slots, items[items['Vol_Rem'] > 0])
    ]

    for pass_name, available_slots, pool_items in passes:
        if pool_items.empty:
            continue
        
        for idx, row in pool_items.iterrows():
            if items.at[idx, 'Vol_Rem'] <= 0:
                continue
            
            density = (row['Kgs'] / row['Vol']) if row['Vol'] > 0 else 0.0
            
            for slot in available_slots:
                rem_vol = items.at[idx, 'Vol_Rem']
                rem_kg = items.at[idx, 'Kgs_Rem']
                if rem_vol <= 0:
                    break
                    
                avail_vol = slot['Max_Vol'] - slot['Used_Vol']
                avail_kg = slot['Max_Kg'] - slot['Used_Kg']
                
                if avail_vol <= 0 or avail_kg <= 0:
                    continue
                    
                is_mnl = any(k in slot['Carrier'].upper() for k in ['CX', 'RH', '5J']) and ('CRK' not in slot['Carrier'].upper())
                is_force = 'FORCE' in pass_name
                
                if row['Is_TikTok'] and is_mnl and not is_force:
                    if mnl_tiktok_kg >= MAX_TIKTOK_MNL_KG:
                        continue
                    else:
                        avail_kg = min(avail_kg, MAX_TIKTOK_MNL_KG - mnl_tiktok_kg)
                        
                vol_fit_by_kg = (avail_kg / density) if density > 0 else rem_vol
                fit_vol = min(rem_vol, avail_vol, vol_fit_by_kg)
                
                if round(fit_vol, 3) <= 0.005:
                    continue
                    
                ratio_kg = (fit_vol / row['Vol']) * row['Kgs'] if row['Vol'] > 0 else 0.0
                
                slot['Used_Vol'] += fit_vol
                slot['Used_Kg'] += ratio_kg
                items.at[idx, 'Vol_Rem'] -= fit_vol
                items.at[idx, 'Kgs_Rem'] -= ratio_kg
                
                if row['Is_TikTok'] and is_mnl:
                    mnl_tiktok_kg += ratio_kg
                    
                truck_str = f" {row['Truck']}" if pd.notna(row['Truck']) else ''
                # 判斷是否因超重被拆板，以顯示(拆箱分配)
                if fit_vol < row['Vol'] - 0.1 and fit_vol < avail_vol:
                    c_desc = f"{row['Customer']}{truck_str} ({int(round(fit_vol))} Vol) (拆箱分配)"
                else:
                    c_desc = f"{row['Customer']}{truck_str} ({int(round(fit_vol))} Vol)"
                
                slot['Items'].append(c_desc)

    plan_rows = []
    for slot in uld_slots:
        used_v = slot['Used_Vol']
        max_v = slot['Max_Vol']
        used_k = slot['Used_Kg']
        max_k = slot['Max_Kg']
        
        util_v = round((used_v / max_v) * 100, 1) if max_v > 0 else 0.0
        
        if slot['Items']:
            items_str = ' + '.join(slot['Items'])
        else:
            if slot['Is_Night']:
                items_str = '預留空板 (Buffer - 夜機預留)'
            else:
                items_str = '預留空板'
                
        plan_rows.append({
            'Carrier': slot['Carrier'],
            'ETD': slot['ETD'],
            'ULD 編號': slot['ULD_ID'],
            'ULD 類型': slot['ULD_Type'],
            '裝載體積 (Vol)': f"{int(used_v)} / {int(max_v)}",
            '裝載毛重 (kg)': f"{round(used_k, 2)} / {int(max_k)} kg",
            '容量利用率': f"{util_v}%",
            '裝載貨物組合': items_str
        })
        
    overflow_items = items[items['Vol_Rem'] >= 0.1]
    if not overflow_items.empty:
        for idx, row in overflow_items.iterrows():
            plan_rows.append({
                'Carrier': '溢出未分配 (Overflow)',
                'ETD': 'N/A',
                'ULD 編號': 'OVERFLOW',
                'ULD 類型': 'N/A',
                '裝載體積 (Vol)': f"{int(row['Vol_Rem'])} Vol",
                '裝載毛重 (kg)': f"{round(row['Kgs_Rem'], 2)} kg",
                '容量利用率': 'N/A',
                '裝載貨物組合': f"⚠️ 物理極限已滿，無法裝載: {row['Customer']} ({int(row['Vol_Rem'])} Vol)"
            })
            
    return pd.DataFrame(plan_rows)

# 4. 轉為 Excel
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
        st.success('✅ 成功解析！使用獨立分板機制 (V6)。')

        col1, col2, col3, col4 = st.columns(4)
        if 'No_of_Carton' in cargo_df.columns:
            col1.metric('總件數 (Cartons)', f"{int(cargo_df['No_of_Carton'].sum()):,} 件")
        col2.metric('總毛重 (Gross Weight)', f"{cargo_df['Kgs'].sum():,.2f} kg")
        col3.metric('總體積 (Total Vol)', f"{int(cargo_df['Vol'].sum()):,} Vol")
        col4.metric('總 ULD 板數配額', f'{len(uld_slots)} 塊/艙')

        # --- 新增加回的貨物清單預覽 ---
        st.subheader('📋 讀取貨物清單預覽')
        st.dataframe(cargo_df)
        
        st.divider() 
        # ------------------------------

        st.subheader('📦 最佳 ULD 打板配載方案結果')
        result_df = generate_uld_plan_v6(cargo_df, uld_slots)

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