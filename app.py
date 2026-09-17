# 2. 升級版：包含「二級回流機制 (Fallback)」的配額限制打板演算法
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

  # 分組 ULD 槽位
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

  # 🆕 定義裝載順序與二級回流 (Fallback) 邏輯
  passes = [
      ("5J", pool_ulds["5J"]),  # 1. 5J 指定貨 -> 塞 5J 專用板
      ("CX", pool_ulds["CX"]),  # 2. CX 指定貨 -> 塞 CX 專用板
      ("CRK", pool_ulds["CRK"]),  # 3. CRK 路線貨 -> 優先塞 SR (CRK) 配額板
      (
          "MAIN",
          pool_ulds["CX"] + pool_ulds["RH"] + pool_ulds["OTHER"],
      ),  # 4. 主線貨 -> 塞 CX/RH
      (
          "CRK_FALLBACK",
          pool_ulds["RH"] + pool_ulds["5J"] + pool_ulds["OTHER"],
      ),  # 🆕 5. CRK 爆滿後，剩餘貨物自動回流至 RH / 5J 空板
  ]

  for pool_name, available_slots in passes:
    if pool_name == "CRK_FALLBACK":
      # 抓取第一輪 CRK 爆滿後未裝完的剩餘貨物
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

  # 輸出打板結果表格
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

  # 處理真·無法分配的溢出貨物
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
              f"⚠️ 所有航司配額全數用盡，無法裝載: {row['Customer']} ({int(row['Vol_Rem'])}"
              " Vol)"
          ),
      })

  return pd.DataFrame(plan_rows)