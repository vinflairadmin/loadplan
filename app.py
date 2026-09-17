def parse_uld_quotas_and_cargo(uploaded_file):
  raw_df = pd.read_excel(uploaded_file, header=None)

  # 1. 先定位下方的「貨物標題列」位置
  target_keywords = [
      'customer',
      'kgs',
      'vol',
      'carton',
      'truck',
      'weight',
      'pcs',
  ]
  cargo_header_idx = None
  for idx, row in raw_df.iterrows():
    row_cells = [str(c).lower() for c in row if pd.notna(c)]
    if sum(1 for kw in target_keywords if any(kw in cell for cell in row_cells)) >= 3:
      cargo_header_idx = idx
      break

  if cargo_header_idx is None:
    raise ValueError('⚠️ 找不到貨物資料標題列！')

  # 2. 定位上方的「CARRIER 配額標題列」位置
  carrier_header_idx = None
  for idx in range(cargo_header_idx):
    row_cells = [str(c).upper() for c in raw_df.iloc[idx] if pd.notna(c)]
    if any('CARRIER' in c for c in row_cells):
      carrier_header_idx = idx
      break

  # 3. 解析 ULD 類型容量對照表 (Cols 10 & 11)
  vol_mapping = {}
  for idx in range(1, cargo_header_idx):
    uld_name = str(raw_df.iloc[idx, 10]).strip()
    vol_val = raw_df.iloc[idx, 11]
    if pd.notna(uld_name) and pd.notna(vol_val):
      try:
        vol_mapping[uld_name] = float(vol_val)
      except:
        pass

  default_vols = {
      'H3': 2000,
      'H3(N1)': 1600,
      'ZX': 1800,
      'X5-03': 1500,
      'X5-29': 1500,
      'AKH': 400,
      'LD3': 600,
      'BULK': 167,
  }
  for k, v in default_vols.items():
    vol_mapping.setdefault(k, v)

  # 4. 🆕 動態讀取 CARRIER 配額（從 carrier_header_idx + 1 一直讀到 cargo_header_idx - 1）
  headers = [
      str(c).split('(')[0].strip()
      for c in raw_df.iloc[carrier_header_idx, 0:9].values
  ]
  uld_slots = []

  for r in range(carrier_header_idx + 1, cargo_header_idx):
    carrier = raw_df.iloc[r, 0]
    if pd.notna(carrier) and str(carrier).strip() != '':
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
                    'Carrier': carrier_str,
                    'ULD_ID': f'{carrier_str}-{uld_type}-{i}',
                    'ULD_Type': uld_type,
                    'Max_Vol': max_v,
                    'Max_Kg': 1800.0 if uld_type in ['H3', 'ZX'] else 1240.0,
                    'Used_Vol': 0.0,
                    'Used_Kg': 0.0,
                    'Items': [],
                })
          except ValueError:
            continue

  # 5. 解析貨物資料 (Cargo DataFrame)
  df = raw_df.iloc[cargo_header_idx + 1 :].copy()
  headers_cargo = [
      str(h).strip() if pd.notna(h) else ''
      for h in raw_df.iloc[cargo_header_idx].values
  ]

  seen = {}
  unique_headers = []
  for h in headers_cargo:
    if h in seen:
      seen[h] += 1
      unique_headers.append(f'{h}_{seen[h]}')
    else:
      seen[h] = 0
      unique_headers.append(h)
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
    elif 'carrier' in c_lower:
      col_mapping[col] = 'Carrier'
    elif 'remark' in c_lower:
      col_mapping[col] = 'Remarks'

  df = df.rename(columns=col_mapping)
  if 'Kgs' in df.columns:
    df['Kgs'] = pd.to_numeric(df['Kgs'], errors='coerce')
  if 'Vol' in df.columns:
    df['Vol'] = pd.to_numeric(df['Vol'], errors='coerce')
  if 'No_of_Carton' in df.columns:
    df['No_of_Carton'] = pd.to_numeric(df['No_of_Carton'], errors='coerce')

  cargo_df = df.dropna(subset=['Vol', 'Kgs']).reset_index(drop=True)
  return cargo_df, uld_slots