from datetime import datetime
import json
import os
import re
import pandas as pd

URL_BASE_CLUSTERVIEW_POR_RED = {
    "CLARO": "https://poccl-01.traxium.net/clusterview/",
    "VTR": "https://poccl-02.traxium.net/clusterview/",
}


def buscar_archivo(nombre_parcial):
  for root, dirs, files in os.walk("."):
    for f in files:
      if nombre_parcial.lower() in f.lower():
        return os.path.join(root, f)
  return nombre_parcial


def limpiar_numerico(val):
  if pd.isna(val):
    return None
  clean_str = re.sub(r"(?i)[^\d\.\-]", "", str(val)).strip()
  try:
    return float(clean_str)
  except ValueError:
    return None


def obtener_marca_ont(marca_orig, serial):
  m = str(marca_orig).upper().strip()
  s = str(serial).upper().strip()
  if "HWTC" in m or "HUAWEI" in m or s.startswith("HWTC"):
    return "Huawei"
  elif "ZTE" in m or "ZTEG" in m or s.startswith("ZTEG"):
    return "ZTE"
  elif (
      "ALCL" in m
      or "NOK" in m
      or "NOKIA" in m
      or "ALCATEL" in m
      or s.startswith("ALCL")
  ):
    return "Nokia / Alcatel"
  elif "FHTT" in m or "FIBERHOME" in m or s.startswith("FHTT"):
    return "Fiberhome"
  elif "SCOM" in m or "SCOM" in s:
    return "SCOM"
  else:
    return "Otras Marcas"


def generar_dashboard():
  print("[INFO] Buscando bases de datos de Claro, VTR y Stock de Equipos...")
  archivo_clch = buscar_archivo("CLCH.csv")
  archivo_vtr = buscar_archivo("VTR.csv")
  archivo_stock = buscar_archivo("Stock Equipos Principales")

  df_clch = pd.read_csv(archivo_clch)
  df_vtr = pd.read_csv(archivo_vtr)

  df_clch["Red"] = "CLARO"
  df_vtr["Red"] = "VTR"

  df_raw = pd.concat([df_clch, df_vtr], ignore_index=True)

  df_stock = None
  if archivo_stock and os.path.exists(archivo_stock):
    try:
      df_stock = pd.read_csv(
          archivo_stock,
          sep=None,
          engine="python",
          dtype=str,
          encoding="latin-1",
      )
    except Exception as e:
      print(f"[ADVERTENCIA] No se pudo leer el stock de equipos: {e}")

  fecha_actualizacion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

  df_raw["UF_default"] = (
      df_raw.get("clu_desc", df_raw.get("olt_desc", "N/A"))
      .astype(str)
      .str.slice(0, 3)
      .str.strip()
      .str.upper()
  )
  df_raw["UF_default"] = df_raw["UF_default"].fillna("N/A")

  df_raw["rx_ont"] = df_raw["optics_rxlevel"].apply(limpiar_numerico)
  df_raw["rx_olt"] = df_raw.get(
      "optics_oltrxontpower", pd.Series([None] * len(df_raw))
  ).apply(limpiar_numerico)
  df_raw["tx_ont"] = df_raw.get(
      "optics_txlevel", pd.Series([None] * len(df_raw))
  ).apply(limpiar_numerico)
  df_raw["bias_ma"] = df_raw["optics_laserbiascurrent"].apply(limpiar_numerico)
  df_raw["temp_c"] = df_raw.get(
      "optics_tempoptical", pd.Series([None] * len(df_raw))
  ).apply(limpiar_numerico)
  df_raw["voltage_mv"] = df_raw.get(
      "optics_voltage", pd.Series([None] * len(df_raw))
  ).apply(limpiar_numerico)
  df_raw["qoe_val"] = df_raw.get(
      "qoe", pd.Series([100.0] * len(df_raw))
  ).apply(limpiar_numerico)

  df_optico = df_raw.dropna(subset=["rx_ont"]).copy()
  if df_optico["rx_olt"].isna().all():
    df_optico["rx_olt"] = df_optico["rx_ont"] - 2.5

  if df_stock is not None and not df_stock.empty:
    stock_serial_col = None
    for col in df_stock.columns:
      if (
          "serie" in col.lower()
          or "serial" in col.lower()
          or "ont" in col.lower()
      ):
        stock_serial_col = col
        break

    if stock_serial_col:
      df_stock["match_serial"] = (
          df_stock[stock_serial_col].astype(str).str.strip().str.upper()
      )
      df_optico["match_serial"] = (
          df_optico["SERIE ONT"].astype(str).str.strip().str.upper()
      )
      df_optico = df_optico.merge(
          df_stock, on="match_serial", how="left", suffixes=("", "_stock")
      )

  df_optico["marca_ont"] = df_optico.apply(
      lambda r: obtener_marca_ont(
          r.get("marca_desc", ""), r.get("SERIE ONT", "")
      ),
      axis=1,
  )

  def evaluar_ont_unificado(row):
    rx = row["rx_ont"]
    olt = row["rx_olt"]
    tx_ont_val = row["tx_ont"] if pd.notna(row["tx_ont"]) else None
    bias = row["bias_ma"] if pd.notna(row["bias_ma"]) else 15.0
    temp = row["temp_c"] if pd.notna(row["temp_c"]) else 35.0
    estado_ont = str(row.get("ESTADO ONT", "")).upper()
    qoe = row["qoe_val"] if pd.notna(row["qoe_val"]) else 100.0

    delta_loss = round(abs(rx) - 20.5, 2)
    if delta_loss < 0:
      delta_loss = 0.0

    asimetria_rx_db = round(olt - rx, 2) if pd.notna(olt) else 0.0
    if tx_ont_val is not None and pd.notna(olt):
      perdida_ds_est = 3.0 - rx
      perdida_us_est = tx_ont_val - olt
      delta_atenuacion_us_ds = round(perdida_us_est - perdida_ds_est, 2)
    else:
      delta_atenuacion_us_ds = round((-1.0) - asimetria_rx_db, 2)

    magnitud_asim = max(abs(asimetria_rx_db), abs(delta_atenuacion_us_ds))
    if magnitud_asim >= 3.5 or (rx >= -25.0 and pd.notna(olt) and olt < -26.0):
      nivel_asimetria = "ALTA"
    elif magnitud_asim >= 1.5:
      nivel_asimetria = "MODERADA"
    else:
      nivel_asimetria = "NORMAL"

    sentido_asim = (
        "US más débil que DS" if asimetria_rx_db < 0 else "US más alto que DS"
    )
    afectacion_asimetria = (
        f"{nivel_asimetria} ({asimetria_rx_db:+.2f} dB | ΔL US-DS"
        f" {delta_atenuacion_us_ds:+.2f} dB | {sentido_asim})"
    )

    orl_val = round(35.0 - (delta_loss * 0.8), 1)
    TEMP_STRESSED_C = 60.0
    delta_temp = round(temp - TEMP_STRESSED_C, 1) if temp >= TEMP_STRESSED_C else 0.0

    vector_bias = bias >= 35.0
    asimetrica = (rx >= -25.0 and pd.notna(olt) and olt < -26.0) or (
        nivel_asimetria == "ALTA"
    )

    is_fec = (
        "Uncorrectable" in str(row.get("uncorrect_code_words", ""))
        or estado_ont == "OFFLINE"
    )
    is_osp = delta_loss >= 3.0
    is_drop = estado_ont in ["OFFLINE", "STRESSED"] or qoe < 60.0
    is_temp = delta_temp >= 10.0
    is_orl = orl_val < 32.0
    is_clima = delta_temp >= 15.0

    tiene_falla = (
        vector_bias
        or asimetrica
        or is_fec
        or is_osp
        or is_drop
        or is_temp
        or is_orl
        or is_clima
    )

    if vector_bias:
      categoria = "4. Vector Bias (> 35mA)"
      accion_corta = "Reemplazo Láser / HW"
      accion_detallada = (
          "Sustitución física del equipo ONT por fin de vida útil."
      )
      observacion_causa = f"Corriente de sesgo crítica ({bias} mA)."
    elif asimetrica:
      categoria = "3. Falla Asimétrica Upstream"
      accion_corta = "Revisión Fusión 1310nm"
      accion_detallada = "Re-fusión de empalmes en caja NAP/FAT."
      observacion_causa = (
          f"Asimetría de subida ({asimetria_rx_db:+.2f} dB). Rx ONT: {rx} dBm /"
          f" OLT: {olt} dBm."
      )
    elif not tiene_falla:
      categoria = "1. Rango Nominal"
      accion_corta = "Normal / Sin Acción"
      accion_detallada = "Enlace operando en rango óptimo."
      observacion_causa = (
          f"Enlace sin anomalías ópticas (Asim. US: {asimetria_rx_db:+.2f} dB)."
      )
    else:
      categoria = "2. Falla ODN / Drop"
      accion_corta = "Inspección Drop / VFL"
      accion_detallada = (
          "Visita domiciliaria para inspección de acometida (drop) y limpieza"
          " con alcohol isopropílico."
      )
      observacion_causa = f"Atenuación pasiva ODN (Rx ONT: {rx} dBm)."

    if nivel_asimetria == "MODERADA" and not asimetrica:
      observacion_causa += (
          f" [Asimetría US moderada: {asimetria_rx_db:+.2f} dB]."
      )

    if estado_ont == "OFFLINE":
      observacion_causa += " [ALERTA: ONT Offline]."

    condicion = "Afectadas" if tiene_falla else "Condición Normal"

    if estado_ont == "OFFLINE":
      impacto = "Offline"
    elif estado_ont == "STRESSED":
      impacto = "Estresadas"
    elif is_fec or vector_bias:
      impacto = "Crítico"
    elif tiene_falla:
      impacto = "Degradado"
    else:
      impacto = "Normal"

    return pd.Series([
        categoria,
        delta_loss,
        accion_detallada,
        accion_corta,
        condicion,
        impacto,
        observacion_causa,
        tiene_falla,
        orl_val,
        delta_temp,
        is_fec,
        is_osp,
        is_drop,
        is_temp,
        is_orl,
        is_clima,
        asimetria_rx_db,
        afectacion_asimetria,
        asimetrica,
    ])

  df_optico[[
      "cuadrante_traxium",
      "delta_loss",
      "macro_accion_detallada",
      "macro_accion_corta",
      "condicion",
      "impacto",
      "observacion_causa",
      "tiene_falla",
      "orl_val",
      "delta_temp_clima",
      "is_fec",
      "is_osp",
      "is_drop",
      "is_temp",
      "is_orl",
      "is_clima",
      "asimetria_rx_db",
      "afectacion_asimetria",
      "is_asimetria_us",
  ]] = df_optico.apply(evaluar_ont_unificado, axis=1)

  df_optico["olt_key"] = (
      df_optico.get("olt_desc", "OLT_Default").astype(str).str.strip()
  )
  df_optico["cluster_key"] = (
      df_optico.get("clu_desc", "Cluster_Default").astype(str).str.strip()
  )

  def limpiar_cluster_id(val):
    if pd.isna(val):
      return ""
    s = str(val).strip()
    if s.upper() in ["", "NAN", "NONE", "N/A", "NULL"]:
      return ""
    if s.endswith(".0"):
      s = s[:-2]
    solo_digitos = re.sub(r"[^\d]", "", s)
    return solo_digitos if solo_digitos else s

  def obtener_cluster_id(row):
    for col in row.index:
      if str(col).strip().lower() in ["clu_id", "cluster_id", "id_cluster", "cluid"]:
        limpio = limpiar_cluster_id(row.get(col))
        if limpio:
          return limpio
    return ""

  df_optico["cluster_id_key"] = df_optico.apply(obtener_cluster_id, axis=1)

  def obtener_puerto_concatenado(row):
    olt = str(row.get("olt_desc", "OLT_Default")).strip()
    pue = str(row.get("port_desc", "Port_Default")).strip()
    if pue.upper() in ["NAN", "NONE", ""]:
      pue = "Port_Default"
    return f"{olt} + {pue}"

  df_optico["puerto_key"] = df_optico.apply(obtener_puerto_concatenado, axis=1)

  def obtener_fat(row):
    red = str(row.get("Red", "")).upper()

    def buscar_valor_col(patrones):
      for col in row.index:
        for pat in patrones:
          if pat.lower() in str(col).lower():
            val = row.get(col)
            if (
                pd.notna(val)
                and str(val).strip() != ""
                and str(val).strip().upper() not in ["NAN", "N/A", "NONE", ""]
            ):
              return str(val).strip()
      return None

    if red == "VTR":
      val_vtr = buscar_valor_col(["Id Nap vtr", "id_nap_vtr", "id nap vtr"])
      return val_vtr if val_vtr else ""
    else:
      val_claro = buscar_valor_col(
          ["Id Nap Claro", "id_nap_claro", "id nap claro"]
      )
      return val_claro if val_claro else ""

  df_optico["fat_key"] = df_optico.apply(obtener_fat, axis=1)
  df_optico["direccion_key"] = (
      df_optico.get("street_address", "Direccion_Default")
      .astype(str)
      .str.strip()
  )

  def obtener_localidad(row):
    def buscar_val(patrones):
      for col in row.index:
        for pat in patrones:
          if pat.lower() in str(col).lower():
            val = row.get(col)
            if (
                pd.notna(val)
                and str(val).strip() != ""
                and str(val).strip().upper() != "NAN"
            ):
              return str(val).strip()
      return None

    loc = buscar_val([
        "Codi Localidad",
        "codi_localidad",
        "codi localidad",
        "localidad",
    ])
    return loc if loc else row.get("UF_default", "N/A")

  df_optico["localidad_key"] = df_optico.apply(obtener_localidad, axis=1)

  olt_stats = df_optico.groupby("olt_key").agg(
      total=("tiene_falla", "count"),
      fail=("tiene_falla", lambda x: (x == True).sum()),
  )
  olt_stats["ratio"] = olt_stats["fail"] / olt_stats["total"]
  olts_falla = set(olt_stats[olt_stats["ratio"] > 0.90].index)

  cluster_stats = df_optico.groupby("cluster_key").agg(
      total=("tiene_falla", "count"),
      fail=("tiene_falla", lambda x: (x == True).sum()),
  )
  cluster_stats["ratio"] = cluster_stats["fail"] / cluster_stats["total"]
  clusters_falla = set(cluster_stats[cluster_stats["ratio"] > 0.90].index)

  puerto_stats = df_optico.groupby(["puerto_key"]).agg(
      total=("tiene_falla", "count"),
      fail=("tiene_falla", lambda x: (x == True).sum()),
  )
  puertos_falla = set(
      puerto_stats[
          (puerto_stats["fail"] == puerto_stats["total"])
          & (puerto_stats["total"] >= 2)
      ].index
  )

  fat_df_valido = df_optico[df_optico["fat_key"] != ""]
  fat_stats = fat_df_valido.groupby(["olt_key", "puerto_key", "fat_key"]).agg(
      total=("tiene_falla", "count"),
      fail=("tiene_falla", lambda x: (x == True).sum()),
  )
  fats_falla = set(
      fat_stats[
          (fat_stats["fail"] == fat_stats["total"])
          & (fat_stats["total"] >= 2)
      ].index
  )

  def asignar_macro(row):
    if not row["tiene_falla"]:
      return "Sin Afectación"

    olt = row["olt_key"]
    clu = row["cluster_key"]
    pue = row["puerto_key"]
    fat = (olt, pue, row["fat_key"])

    if olt in olts_falla:
      return "Falla OLT"
    elif clu in clusters_falla:
      return "Falla Clúster"
    elif pue in puertos_falla:
      return "Puerto (OTDR)"
    elif row["fat_key"] != "" and fat in fats_falla:
      return "FAT Afectada"
    else:
      return "Falla Cliente"

  df_optico["macro_hallazgo"] = df_optico.apply(asignar_macro, axis=1)

  def ajustar_acciones_macro(row):
    macro = row["macro_hallazgo"]
    if macro == "Falla OLT":
      return (
          "Inspección Troncal OLT",
          "Inspección y medición en ODF de cabecera.",
      )
    elif macro == "Falla Clúster":
      return (
          "Revisión Alimentador Clúster",
          "Intervención en planta externa sobre el cable alimentador.",
      )
    elif macro == "Puerto (OTDR)":
      return (
          "Inspección Puerto ODF",
          "Despachar técnico con OTDR al nodo/distribuidor óptico.",
      )
    elif macro == "FAT Afectada":
      return (
          "Inspección Splitter FAT",
          "Inspección física en caja FAT/NAP de planta externa.",
      )
    else:
      return row["macro_accion_corta"], row["macro_accion_detallada"]

  df_optico[["macro_accion_corta", "macro_accion_detallada"]] = (
      df_optico.apply(ajustar_acciones_macro, axis=1, result_type="expand")
  )

  puntos_matriz = []
  for _, r in df_optico.iterrows():
    red = str(r["Red"]).strip()
    serial = str(r.get("SERIE ONT", "N/A")).strip()
    zona = str(r["localidad_key"]).strip()
    olt = str(r["olt_key"])
    cluster = str(r["cluster_key"])
    cluster_id = str(r.get("cluster_id_key", "")).strip()
    puerto = str(r["puerto_key"])
    fat_num = str(r["fat_key"])
    direccion = str(r["direccion_key"])
    distancia = str(r.get("distance", "0 Km")).strip()

    fec_st = (
        "Uncorrectable CodeWords (Sin Señal)"
        if r["impacto"] == "Offline"
        else (
            "FEC High (Corrected Error Rate)"
            if r["is_fec"]
            else "FEC Normal"
        )
    )

    es_correctiva = (
        r["is_fec"]
        or r["is_drop"]
        or (r["bias_ma"] is not None and r["bias_ma"] >= 35.0)
        or r["macro_hallazgo"]
        in ["Falla OLT", "Falla Clúster", "Puerto (OTDR)", "FAT Afectada"]
    )

    if not r["tiene_falla"]:
      tipo_atencion = "Sin Acción"
    elif es_correctiva:
      tipo_atencion = "Correctiva (Inmediata)"
    else:
      tipo_atencion = "Preventiva (Proactiva de Red)"

    puntos_matriz.append({
        "r": red,
        "x": round(r["rx_ont"], 2),
        "y": round(r["rx_olt"], 2),
        "t": round(r["tx_ont"], 2) if pd.notna(r["tx_ont"]) else 0.0,
        "b": round(r["bias_ma"], 2) if pd.notna(r["bias_ma"]) else 0.0,
        "tp": round(r["temp_c"], 1) if pd.notna(r["temp_c"]) else 0.0,
        "v": round(r["voltage_mv"], 1) if pd.notna(r["voltage_mv"]) else 0.0,
        "q": round(r["qoe_val"], 1) if pd.notna(r["qoe_val"]) else 100.0,
        "dl": r["delta_loss"],
        "fs": fec_st,
        "dt": r["delta_temp_clima"],
        "or": r["orl_val"],
        "sp": (
            "ALTA (NOC)" if r["macro_hallazgo"] in ["Falla OLT"] else "Estándar"
        ),
        "mo": r["marca_ont"],
        "cr": r["observacion_causa"],
        "ac": r["macro_accion_corta"],
        "ad": r["macro_accion_detallada"],
        "co": r["condicion"],
        "im": r["impacto"],
        "mh": r["macro_hallazgo"],
        "ta": tipo_atencion,
        "sr": serial,
        "zo": zona,
        "ol": olt,
        "cl": cluster,
        "cid": cluster_id,
        "pu": puerto,
        "fa": fat_num,
        "di": direccion,
        "ds": distancia,
        "cat": r["cuadrante_traxium"],
        "tf": bool(r["tiene_falla"]),
        "ifec": bool(r["is_fec"]),
        "iosp": bool(r["is_osp"]),
        "idrop": bool(r["is_drop"]),
        "itemp": bool(r["is_temp"]),
        "iorl": bool(r["is_orl"]),
        "icli": bool(r["is_clima"]),
        "adbm": round(r["asimetria_rx_db"], 2),
        "afus": r["afectacion_asimetria"],
        "iasim": bool(r["is_asimetria_us"]),
    })

  html_template = r"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Gerencial y Operativo FTTH - Claro & VTR</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-zoom/2.0.1/chartjs-plugin-zoom.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
    <style>
        :root {
            --bg-dark: #0f172a;
            --panel-bg: #1e293b;
            --text-light: #f8fafc;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-orange: #f59e0b;
            --accent-purple: #a855f7;
            --accent-cyan: #06b6d4;
            --accent-pink: #ec4899;
            --border-color: #334155;
        }
        body { margin: 0; padding: 20px; background-color: var(--bg-dark); color: var(--text-light); font-family: 'Segoe UI', system-ui, sans-serif; }
        .header-container { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--border-color); padding-bottom: 15px; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }
        .header-title h1 { margin: 0; font-size: 21px; color: #38bdf8; }
        .header-subtitle { font-size: 12px; color: #94a3b8; margin-top: 4px; }
        .timestamp-badge { background: #090d16; border: 1px solid var(--border-color); padding: 8px 14px; border-radius: 6px; font-size: 12px; color: #38bdf8; }

        .tabs-nav { display: flex; gap: 10px; margin-bottom: 18px; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; }
        .tab-btn { background: #1e293b; color: #94a3b8; border: 1px solid var(--border-color); padding: 10px 20px; border-radius: 6px; font-size: 13px; font-weight: 700; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; gap: 8px; }
        .tab-btn:hover { background: #263548; color: #f8fafc; }
        .tab-btn.active { background: #0284c7; color: #ffffff; border-color: #38bdf8; box-shadow: 0 2px 10px rgba(2, 132, 199, 0.4); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .filter-bar { background: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 15px; display: flex; gap: 15px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
        .filter-group { display: flex; flex-direction: column; gap: 5px; flex: 1; min-width: 140px; }
        .filter-group label { font-size: 11px; font-weight: bold; color: #94a3b8; text-transform: uppercase; }
        .filter-group select { background: #0f172a; color: #f8fafc; border: 1px solid var(--border-color); padding: 8px 12px; border-radius: 6px; font-size: 13px; outline: none; }
        .btn-reset { background: #0284c7; color: white; border: none; padding: 9px 16px; border-radius: 6px; font-size: 12px; font-weight: bold; cursor: pointer; align-self: flex-end; }
        .btn-reset:hover { background: #0369a1; }

        .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 12px; }
        .kpi-card { background: var(--panel-bg); border-radius: 8px; padding: 12px 15px; border: 1px solid var(--border-color); border-left: 5px solid var(--border-color); cursor: pointer; transition: transform 0.2s; position: relative; }
        .kpi-card:hover { transform: translateY(-2px); background: #263548; z-index: 20; }
        .active-filter { background: #334155 !important; border: 1px solid #38bdf8 !important; }
        .kpi-header-row { display: flex; justify-content: space-between; align-items: center; gap: 5px; }
        .kpi-title { font-size: 10.5px; text-transform: uppercase; color: #94a3b8; font-weight: 600; }
        .kpi-value { font-size: 18px; font-weight: 700; margin-top: 4px; white-space: nowrap; }

        .exec-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .exec-card { background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%); border: 1px solid var(--border-color); border-radius: 10px; padding: 16px 20px; border-top: 4px solid #38bdf8; box-shadow: 0 4px 12px rgba(0,0,0,0.25); }
        .exec-card-title { font-size: 11px; text-transform: uppercase; color: #94a3b8; font-weight: 700; letter-spacing: 0.5px; }
        .exec-card-val { font-size: 26px; font-weight: 800; margin: 6px 0; color: #f8fafc; }
        .exec-card-desc { font-size: 11.5px; color: #cbd5e1; line-height: 1.35; }

        .charts-main-grid { display: grid; grid-template-columns: 1.45fr 1fr; gap: 20px; margin-bottom: 20px; }
        @media (max-width: 1150px) { .charts-main-grid { grid-template-columns: 1fr; } }
        .chart-panel { background: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; display: flex; flex-direction: column; overflow: visible; }
        .chart-panel-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 15px; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; gap: 10px; flex-wrap: wrap; }
        .chart-panel-title { font-size: 14px; font-weight: 700; color: #38bdf8; text-transform: uppercase; margin: 0; }
        .chart-panel-sub { font-size: 11.5px; color: #94a3b8; margin-top: 3px; }
        .donut-layout { display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 16px; align-items: center; flex: 1; overflow: visible; }
        @media (max-width: 820px) { .donut-layout { grid-template-columns: 1fr; } }

        .donut-dark-showcase {
            position: relative;
            width: 100%;
            height: 390px;
            background: radial-gradient(circle at center, #182234 0%, #0d1424 100%);
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 12px;
            box-sizing: border-box;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: visible;
        }
        .donut-center-overlay {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            pointer-events: none;
            z-index: 1;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 50%;
            width: 88px;
            height: 88px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.5);
            transition: top 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
        }
        .donut-center-overlay.below-tooltip {
            top: calc(100% - 54px);
            transform: translate(-50%, 0);
            width: 112px;
            height: 62px;
            border-radius: 12px;
            z-index: 1;
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.65);
        }
        .donut-dark-showcase canvas { position: relative; z-index: 2; }
        .donut-center-title { font-size: 8.5px; font-weight: 800; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.4px; }
        .donut-center-val { font-size: 16px; font-weight: 900; color: #f8fafc; margin-top: 2px; }
        .donut-center-sub { font-size: 8px; font-weight: 600; color: #38bdf8; }

        .breakdown-c-panel {
            background: #090d16;
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 12px 14px;
            font-size: 11.5px;
            height: 390px;
            box-sizing: border-box;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .breakdown-section-title {
            font-weight: 700;
            font-size: 10.5px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            padding-bottom: 4px;
            margin-bottom: 4px;
            border-bottom: 1px solid #1e293b;
            display: flex;
            justify-content: space-between;
        }
        .legend-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 3px 6px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.15s;
        }
        .legend-item:hover { background: #1e293b; }
        .legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 6px; flex-shrink: 0; }

        .fault-bar-row { margin-bottom: 7px; }
        .fault-bar-header { display: flex; justify-content: space-between; font-size: 11px; color: #e2e8f0; margin-bottom: 2px; }
        .fault-bar-meta { color: #94a3b8; font-size: 10.5px; }
        .fault-bar-track { width: 100%; height: 6px; background: #1e293b; border-radius: 999px; overflow: hidden; }
        .fault-bar-fill { height: 100%; border-radius: 999px; transition: width 0.25s ease; }

        .parallel-stacked-panel {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            overflow: visible;
        }
        .mode-toggle-group {
            display: inline-flex;
            background: #090d16;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 3px;
            gap: 3px;
        }
        .mode-toggle-btn {
            background: transparent;
            color: #94a3b8;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s;
        }
        .mode-toggle-btn.active { background: #0284c7; color: #ffffff; }
        .parallel-legend-bar {
            display: flex;
            flex-wrap: wrap;
            gap: 12px 18px;
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 14px;
            font-size: 11.5px;
        }
        .parallel-legend-item { display: inline-flex; align-items: center; gap: 6px; color: #e2e8f0; }
        .parallel-bars-box {
            position: relative;
            width: 100%;
            height: 220px;
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 12px 16px;
            box-sizing: border-box;
            overflow: visible;
        }
        .parallel-summary-strip {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 12px;
            margin-top: 12px;
        }
        .parallel-category-card {
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 11.5px;
        }
        .parallel-category-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 700;
            margin-bottom: 6px;
            padding-bottom: 4px;
            border-bottom: 1px solid #1e293b;
        }
        .parallel-chips-wrap { display: flex; flex-wrap: wrap; gap: 6px; }
        .parallel-chip {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 10.5px;
            color: #f8fafc;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }

        .jerarquias-scroll-container {
            position: relative;
            width: 100%;
            height: 390px;
            overflow-y: auto;
            overflow-x: hidden;
            border: 1px solid #1e293b;
            border-radius: 8px;
            background: #090d16;
            padding: 8px 10px;
            box-sizing: border-box;
        }
        .jerarquias-canvas-wrapper { position: relative; width: 100%; min-height: 370px; }
        .jerarquias-badge-count {
            background: #0f172a;
            border: 1px solid #334155;
            color: #38bdf8;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            white-space: nowrap;
        }
        .jerarquias-nav-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }
        .btn-jerarquia-back {
            background: #0284c7;
            color: #ffffff;
            border: 1px solid #38bdf8;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            transition: background 0.15s;
        }
        .btn-jerarquia-back:hover { background: #0369a1; }
        .btn-traxium-cluster {
            background: #0f766e;
            color: #ffffff;
            border: 1px solid #2dd4bf;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            transition: background 0.15s, transform 0.15s;
        }
        .btn-traxium-cluster:hover { background: #115e59; transform: translateY(-1px); }
        .crumb-cluster-id-pill {
            background: rgba(15, 118, 110, 0.25);
            border: 1px solid #2dd4bf;
            color: #99f6e4;
            padding: 2px 7px;
            border-radius: 4px;
            font-weight: 700;
            font-family: monospace;
        }
        .jerarquias-breadcrumb {
            display: none;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
            background: #090d16;
            border: 1px solid #334155;
            border-radius: 6px;
            padding: 6px 10px;
            margin-bottom: 10px;
            font-size: 11px;
            color: #94a3b8;
        }
        .crumb-current { color: #f8fafc; font-weight: 700; }
        .crumb-selected-pill {
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid #38bdf8;
            color: #e0f2fe;
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 10.5px;
            font-weight: 700;
        }

        .canvas-box-sm { position: relative; width: 100%; height: 260px; overflow: visible; }
        .charts-secondary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .badge-action { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }

        .tooltip-icon { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; background: #334155; color: #38bdf8; border-radius: 50%; font-size: 10px; font-weight: bold; cursor: help; position: relative; flex-shrink: 0; }
        .tooltip-box { visibility: hidden; width: 340px; max-width: 85vw; background: #090d16; color: #f8fafc; text-align: left; border-radius: 8px; padding: 12px 14px; position: absolute; z-index: 2000; bottom: 130%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.18s; border: 1px solid #38bdf8; font-size: 11px; font-weight: normal; box-shadow: 0 8px 24px rgba(0,0,0,0.75); pointer-events: none; line-height: 1.45; }
        .kpi-card:nth-last-child(-n+2) .tooltip-box, .kpi-card.right-align-tooltip .tooltip-box { left: auto; right: 0; transform: none; }
        .tooltip-icon:hover .tooltip-box { visibility: visible; opacity: 1; }
        .tooltip-box .tt-title { font-weight: 800; color: #38bdf8; margin-bottom: 6px; font-size: 12px; border-bottom: 1px solid #334155; padding-bottom: 4px; }
        .tooltip-box .tt-row { margin-top: 4px; }
        .tooltip-box .tt-formula { background: #1e293b; border-left: 3px solid #38bdf8; padding: 4px 7px; border-radius: 4px; font-family: Consolas, monospace; font-size: 10.5px; color: #e2e8f0; margin: 4px 0; }
        .tooltip-box .tt-threshold { color: #fbbf24; font-weight: 600; }

        .table-panel { background: var(--panel-bg); border-radius: 8px; padding: 20px; border: 1px solid var(--border-color); }
        .table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }
        .pagination-bar { display: flex; align-items: center; gap: 10px; font-size: 12px; color: #94a3b8; flex-wrap: wrap; }
        .pagination-bar button { background: #0f172a; color: #38bdf8; border: 1px solid var(--border-color); padding: 5px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; }
        .table-container { max-height: 550px; overflow-y: auto; overflow-x: auto; border: 1px solid var(--border-color); border-radius: 6px; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }
        th, td { padding: 10px 12px; border-bottom: 1px solid var(--border-color); white-space: nowrap; }
        th { background: #0f172a; color: #38bdf8; position: sticky; top: 0; z-index: 10; }
        tr:hover { background: rgba(56, 189, 248, 0.05); }
        .clickable-row { cursor: pointer; }
        .clickable-row:hover { background: rgba(56, 189, 248, 0.15) !important; }
        .btn-excel { background: #10b981; color: white; border: none; padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: bold; cursor: pointer; }
        .btn-drill { background: #0284c7; color: white; border: none; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; cursor: pointer; }
        .btn-drill:hover { background: #0369a1; }

        .sortable-th { cursor: pointer; user-select: none; }
        .sortable-th:hover { background: #1e293b; }
        .sort-arrow { margin-left: 5px; font-size: 10px; color: #94a3b8; }
        .filter-row th { background: #131c2e; top: 37px; z-index: 9; padding: 5px 6px; }
        .col-search-input {
            width: 100%;
            min-width: 78px;
            box-sizing: border-box;
            background: #090d16;
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 4px;
            padding: 4px 6px;
            font-size: 11px;
            outline: none;
        }
        .col-search-input:focus { border-color: #38bdf8; }
        .global-table-input {
            background: #090d16;
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 4px;
            padding: 5px 10px;
            font-size: 12px;
            min-width: 190px;
            outline: none;
        }
        .global-table-input:focus { border-color: #38bdf8; }

        .summary-wrapper { display: flex !important; flex-direction: row !important; gap: 15px; margin-top: 15px; margin-bottom: 20px; width: 100%; box-sizing: border-box; }
        .summary-panel { background: #090d16; border-radius: 8px; padding: 15px; border: 1px solid #38bdf8; flex: 1 !important; width: 48% !important; display: none; box-sizing: border-box; }
        .summary-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .summary-title { font-size: 12px; font-weight: bold; color: #38bdf8; text-transform: uppercase; }
        .summary-close { background: #ef4444; color: white; border: none; border-radius: 4px; padding: 3px 8px; cursor: pointer; font-size: 10px; font-weight: bold; }
        .summary-table-total-row { background: #0f172a; font-weight: bold; color: #38bdf8; border-top: 2px solid #38bdf8; }
        .runtime-error-banner { display: none; background: #450a0a; border: 1px solid #ef4444; color: #fecaca; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 12px; }
    </style>
</head>
<body>
    <div id="runtimeErrorBanner" class="runtime-error-banner"></div>

    <div class="header-container">
        <div class="header-title">
            <h1>CENTRO DE CONTROL OPERATIVO FTTH & TELEMETRÍA TRAXIUM <span style="background: #0284c7; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">Claro & VTR</span></h1>
            <div class="header-subtitle">Diagnóstico Proactivo de Red (PNNM) • Estándar CableLabs • Gestión de Planta Externa (OSP) y Última Milla</div>
        </div>
        <div class="timestamp-badge"><b>Actualización:</b> __FECHA_ACTUALIZACION__</div>
    </div>

    <div class="tabs-nav">
        <button class="tab-btn active" id="btn-tab-gerencial" onclick="switchTab('gerencial')">
            <span>1. Resumen Gerencial (Ejecutivo)</span>
        </button>
        <button class="tab-btn" id="btn-tab-operativo" onclick="switchTab('operativo')">
            <span>2. Matriz Operativa & Telemetría Detallada</span>
        </button>
    </div>

    <div class="filter-bar">
        <div class="filter-group">
            <label>Red / Operación</label>
            <select id="filtroRed" onchange="onFilterChanged()">
                <option value="TODAS">-- Todas las Redes --</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Localidad / Sector</label>
            <select id="filtroLocalidad" onchange="onFilterChanged()">
                <option value="TODAS">-- Todas las Localidades --</option>
            </select>
        </div>
        <div class="filter-group">
            <label>OLT</label>
            <select id="filtroOlt" onchange="onFilterChanged()">
                <option value="TODAS">-- Todas las OLTs --</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Cluster</label>
            <select id="filtroCluster" onchange="onFilterChanged()">
                <option value="TODOS">-- Todos los Clusters --</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Puerto PON</label>
            <select id="filtroPuerto" onchange="onFilterChanged()">
                <option value="TODOS">-- Todos los Puertos --</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Condición</label>
            <select id="filtroCondicion" onchange="onFilterChanged()">
                <option value="TODAS">-- Todas las Condiciones --</option>
                <option value="Afectadas">Afectadas</option>
                <option value="Condición Normal">Condición Normal</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Nivel de Impacto</label>
            <select id="filtroImpacto" onchange="onFilterChanged()">
                <option value="TODOS">-- Todos los Impactos --</option>
                <option value="Offline">Offline</option>
                <option value="Crítico">Crítico</option>
                <option value="Estresadas">Estresadas</option>
                <option value="Degradado">Degradado</option>
                <option value="Normal">Normal</option>
            </select>
        </div>
        <button class="btn-reset" onclick="resetFilters()">Reset Filtros</button>
    </div>

    <div id="tab-gerencial" class="tab-content active">
        <div class="exec-kpi-grid">
            <div class="exec-card" style="border-top-color: #10b981;">
                <div class="exec-card-title">Disponibilidad & Salud Óptica</div>
                <div class="exec-card-val" id="execSaludPct" style="color: #10b981;">0.0%</div>
                <div class="exec-card-desc" id="execSaludDesc">0 de 0 ONTs operando en rango nominal CableLabs.</div>
            </div>
            <div class="exec-card" style="border-top-color: #ef4444;">
                <div class="exec-card-title">ONTs con Problemas</div>
                <div class="exec-card-val" id="execProblemasPct" style="color: #ef4444;">0.0%</div>
                <div class="exec-card-desc" id="execProblemasDesc">0 de 0 ONTs operando con problemas.</div>
            </div>
            <div class="exec-card" style="border-top-color: #ef4444;">
                <div class="exec-card-title">Afectación Masiva de Red (OSP)</div>
                <div class="exec-card-val" id="execRedPct" style="color: #ef4444;">0.0%</div>
                <div class="exec-card-desc" id="execRedDesc">0 ONTs impactadas por OLT, Clúster, Puerto PON o FAT.</div>
            </div>
            <div class="exec-card" style="border-top-color: #38bdf8;">
                <div class="exec-card-title">Fallas Unitarias (Última Milla)</div>
                <div class="exec-card-val" id="execUnitPct" style="color: #38bdf8;">0.0%</div>
                <div class="exec-card-desc" id="execUnitDesc">0 ONTs con anomalía individual (Drop, Bias, Asimetría).</div>
            </div>
            <div class="exec-card" style="border-top-color: #f59e0b;">
                <div class="exec-card-title">Eficiencia Despacho Cuadrillas</div>
                <div class="exec-card-val" id="execRatioOt" style="color: #f59e0b;">0.0x</div>
                <div class="exec-card-desc" id="execRatioDesc">0 elementos de red (OLT/Clúster/Puerto/FAT) concentran fallas masivas.</div>
            </div>
        </div>

        <div class="charts-main-grid">
            <div class="chart-panel">
                <div class="chart-panel-header">
                    <div>
                        <h3 class="chart-panel-title">Distribución de Fallas: Red vs. Unitarias</h3>
                        <div class="chart-panel-sub">Anillo Interior: Red vs. Unitaria • Anillo Exterior: Jerarquía / Categoría • Lateral: Desglose por Tipo de Falla</div>
                    </div>
                </div>
                <div class="donut-layout">
                    <div class="donut-dark-showcase">
                        <canvas id="chartDonaMultinivel"></canvas>
                        <div class="donut-center-overlay" id="donutCenterOverlay">
                            <span class="donut-center-title">Total Fallas</span>
                            <span class="donut-center-val" id="donaCenterTotal">0</span>
                            <span class="donut-center-sub">ONTs Afectadas</span>
                        </div>
                    </div>
                    <div class="breakdown-c-panel" id="donaBreakdownPanel"></div>
                </div>
            </div>

            <div class="chart-panel">
                <div class="chart-panel-header">
                    <div>
                        <h3 class="chart-panel-title">Top Jerarquías Críticas (RED-OLT-CLUSTER)</h3>
                        <div class="chart-panel-sub">Clic en barra para filtrar • Clic nuevamente o en "Volver atrás" conservando selección y leyenda</div>
                    </div>
                    <div class="jerarquias-nav-bar">
                        <button type="button" class="btn-traxium-cluster" id="btnAbrirTraxiumCluster" onclick="openSelectedClusterInTraxium()" style="display:none;" title="Abre la vista del clúster en Traxium en una nueva pestaña">Abrir en Traxium (ClusterView) &#8599;</button>
                        <button type="button" class="btn-jerarquia-back" id="btnJerarquiaBack" onclick="stepBackJerarquiaFilter()" style="display:none;">&larr; Volver atrás</button>
                        <span class="jerarquias-badge-count" id="jerarquiasTotalBadge">0 Clústeres</span>
                    </div>
                </div>
                <div class="jerarquias-breadcrumb" id="jerarquiasBreadcrumb"></div>
                <div class="jerarquias-scroll-container" id="jerarquiasScrollContainer">
                    <div class="jerarquias-canvas-wrapper" id="jerarquiasCanvasWrapper">
                        <canvas id="chartTopJerarquias"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <div class="parallel-stacked-panel">
            <div class="chart-panel-header" style="margin-bottom: 12px;">
                <div>
                    <h3 class="chart-panel-title">Evaluación Paralela: Barras Horizontales Apiladas (Red vs. Unitarias)</h3>
                    <div class="chart-panel-sub">Dos barras principales segmentadas por subcategorías con leyenda superior limpia</div>
                </div>
                <div class="mode-toggle-group" role="group" aria-label="Modo de visualización de barras">
                    <button type="button" class="mode-toggle-btn active" id="btnModePct" onclick="setParallelBarMode('pct')">100% Proporcional</button>
                    <button type="button" class="mode-toggle-btn" id="btnModeAbs" onclick="setParallelBarMode('abs')">Valores Absolutos (ONTs)</button>
                </div>
            </div>
            <div class="parallel-legend-bar" id="parallelStackedLegend"></div>
            <div class="parallel-bars-box">
                <canvas id="chartBarrasApiladasParalelo"></canvas>
            </div>
            <div class="parallel-summary-strip" id="parallelSummaryStrip"></div>
        </div>

        <div class="charts-secondary-grid">
            <div class="chart-panel">
                <div class="chart-panel-header">
                    <div>
                        <h3 class="chart-panel-title">Salud y Prioridad de Atención por Red</h3>
                        <div class="chart-panel-sub">Comparativa Claro vs. VTR (Normal, Preventiva, Correctiva)</div>
                    </div>
                </div>
                <div class="canvas-box-sm">
                    <canvas id="chartOperacionAtencion"></canvas>
                </div>
            </div>

            <div class="chart-panel">
                <div class="chart-panel-header">
                    <div>
                        <h3 class="chart-panel-title">Indicadores CableLabs / PNNM Activos</h3>
                        <div class="chart-panel-sub">Volumen de anomalías físicas y capa 2 detectadas</div>
                    </div>
                </div>
                <div class="canvas-box-sm">
                    <canvas id="chartCableLabsExec"></canvas>
                </div>
            </div>

            <div class="chart-panel">
                <div class="chart-panel-header">
                    <div>
                        <h3 class="chart-panel-title">Desempeño por Fabricante CPE (ONT)</h3>
                        <div class="chart-panel-sub">Proporción de equipos Nominales vs. Con Falla por marca</div>
                    </div>
                </div>
                <div class="canvas-box-sm">
                    <canvas id="chartMarcasExec"></canvas>
                </div>
            </div>
        </div>

        <div class="table-panel" style="border-top: 3px solid #f59e0b;">
            <div class="table-header">
                <div>
                    <h3 style="color: #f59e0b; margin: 0;">PRIORIZACIÓN EJECUTIVA DE INTERVENCIÓN EN PLANTA EXTERNA (TOP FOCOS DE RED)</h3>
                    <div style="font-size: 11.5px; color: #94a3b8; margin-top: 3px;">Elementos jerárquicos agrupados por mayor impacto de clientes para despacho inmediato de cuadrillas</div>
                </div>
                <button class="btn-excel" onclick="exportToExcel('gerencial_focos')">Exportar Plan de Acción (.xlsx)</button>
            </div>
            <div class="table-container" style="max-height: 360px;">
                <table>
                    <thead>
                        <tr>
                            <th>Prioridad</th>
                            <th>Red</th>
                            <th>Jerarquía Afectada</th>
                            <th>Elemento de Red</th>
                            <th>Localidad</th>
                            <th>ONTs Afectadas / Total</th>
                            <th>% Afectación</th>
                            <th>Acción CableLabs Recomendada</th>
                            <th>Inspección</th>
                        </tr>
                    </thead>
                    <tbody id="execPriorityTableBody"></tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="tab-operativo" class="tab-content">
        <div class="kpi-grid" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));">
            <div class="kpi-card" id="card-TODOS" onclick="filterBySymptom('TODOS')" style="border-left-color: #38bdf8;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Total ONTs Evaluadas</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Total ONTs Evaluadas</div>
                        <div class="tt-row"><b>Concepto:</b> Universo de equipos CPE con potencia óptica Rx ONT válida en el ciclo actual.</div>
                        <div class="tt-formula">Total = COUNT(rx_ont IS NOT NULL)</div>
                        <div class="tt-row tt-threshold">Umbral: Se excluyen registros sin lectura Rx ONT numérica.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiTotal" style="color: #38bdf8;">0</div>
            </div>
            <div class="kpi-card" id="card-FAVOR" onclick="filterByFallas()" style="border-left-color: #ef4444;">
                <div class="kpi-header-row">
                    <span class="kpi-title">ONTs con Fallas</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">ONTs con Fallas</div>
                        <div class="tt-row"><b>Concepto:</b> Equipos con al menos una condición anómala activa en óptica, láser, capa 2 o temperatura.</div>
                        <div class="tt-formula">Falla = Bias OR Asimetría OR FEC OR OSP OR Drop OR Temp OR ORL OR Clima</div>
                        <div class="tt-row tt-threshold">Umbral: Cualquier regla de falla en TRUE marca la ONT como "Afectadas".</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiFallas" style="color: #ef4444;">0</div>
            </div>
            <div class="kpi-card" id="card-1. Rango Nominal" onclick="filterBySymptom('1. Rango Nominal')" style="border-left-color: var(--accent-green);">
                <div class="kpi-header-row">
                    <span class="kpi-title">1. Rango Nominal</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">1. Rango Nominal</div>
                        <div class="tt-row"><b>Concepto:</b> ONTs sin ninguna anomalía detectada en los parámetros evaluados.</div>
                        <div class="tt-formula">Nominal = (tiene_falla == FALSE)</div>
                        <div class="tt-row tt-threshold">Umbral: ΔL &lt; 3.0 dB, Bias &lt; 35 mA, ΔT &lt; 10°C, ORL ≥ 32 dB, Asimetría != ALTA, QoE ≥ 60 y estado != OFFLINE/STRESSED.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiOptimo" style="color: var(--accent-green);">0</div>
            </div>
            <div class="kpi-card" id="card-Correctiva" onclick="filterByAttentionType('Correctiva (Inmediata)')" style="border-left-color: #ef4444;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Correctiva (Inmediata)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Atención Correctiva (Inmediata)</div>
                        <div class="tt-row"><b>Concepto:</b> Requiere despacho o intervención urgente por degradación severa o falla masiva.</div>
                        <div class="tt-formula">Correctiva = is_fec OR is_drop OR (bias_ma ≥ 35) OR macro IN [OLT, Clúster, Puerto, FAT]</div>
                        <div class="tt-row tt-threshold">Umbral: Bias ≥ 35 mA, QoE &lt; 60, estado OFFLINE/STRESSED, FEC Uncorrectable o falla macro de red.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiCorrectiva" style="color: #ef4444;">0</div>
            </div>
            <div class="kpi-card" id="card-Preventiva" onclick="filterByAttentionType('Preventiva (Proactiva de Red)')" style="border-left-color: #f59e0b;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Preventiva (Proactiva)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Atención Preventiva (Proactiva)</div>
                        <div class="tt-row"><b>Concepto:</b> Degradación pasiva o térmica temprana antes de corte total del servicio.</div>
                        <div class="tt-formula">Preventiva = tiene_falla AND NOT Correctiva</div>
                        <div class="tt-row tt-threshold">Umbral: ΔL ≥ 3.0 dB, ORL &lt; 32 dB, ΔT ≥ 10°C o Asimetría ALTA sin condición correctiva inmediata.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiPreventiva" style="color: #f59e0b;">0</div>
            </div>
            <div class="kpi-card right-align-tooltip" id="card-4. Vector Bias (> 35mA)" onclick="filterBySymptom('4. Vector Bias (> 35mA)')" style="border-left-color: var(--accent-purple);">
                <div class="kpi-header-row">
                    <span class="kpi-title">4. Vector Bias (> 35mA)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">4. Vector Bias (&gt; 35mA)</div>
                        <div class="tt-row"><b>Concepto:</b> Corriente de polarización del láser transmisor (Tx) elevada por envejecimiento del diodo.</div>
                        <div class="tt-formula">Bias_Eval = optics_laserbiascurrent (mA)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: bias_ma ≥ 35.0 mA (requiere reemplazo de ONT/HW).</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiLaser" style="color: var(--accent-purple);">0</div>
            </div>
        </div>

        <div class="kpi-grid" style="margin-bottom: 12px;">
            <div class="kpi-card" id="macro-card-Falla OLT" onclick="filterByMacroCard('Falla OLT')" style="border-left-color: #dc2626;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Falla OLT</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Falla OLT (Cabecera)</div>
                        <div class="tt-row"><b>Concepto:</b> Afectación masiva a nivel de equipo OLT.</div>
                        <div class="tt-formula">Ratio_OLT = ONTs_con_falla(OLT) / Total_ONTs(OLT)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: Ratio_OLT &gt; 0.90 (&gt;90% de ONTs de la OLT afectadas).</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiOlt" style="color: #dc2626; font-size: 15px;">0</div>
            </div>
            <div class="kpi-card" id="macro-card-Falla Clúster" onclick="filterByMacroCard('Falla Clúster')" style="border-left-color: #ea580c;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Falla Clúster</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Falla Clúster (Zonal / Alimentador)</div>
                        <div class="tt-row"><b>Concepto:</b> Afectación concentrada en un clúster de red.</div>
                        <div class="tt-formula">Ratio_Cluster = ONTs_con_falla(Cluster) / Total_ONTs(Cluster)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: Ratio_Cluster &gt; 0.90 (&gt;90% de ONTs del clúster afectadas).</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiCluster" style="color: #ea580c; font-size: 15px;">0</div>
            </div>
            <div class="kpi-card" id="macro-card-Puerto (OTDR)" onclick="filterByMacroCard('Puerto (OTDR)')" style="border-left-color: #ef4444;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Puerto (OTDR)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Puerto PON con Falla Total (OTDR)</div>
                        <div class="tt-row"><b>Concepto:</b> Corte o degradación total de un puerto PON.</div>
                        <div class="tt-formula">Falla_Puerto = (ONTs_falla == Total_ONTs) AND (Total_ONTs ≥ 2)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: 100% de ONTs afectadas en el puerto con ≥ 2 abonados.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiPuerto" style="color: #ef4444; font-size: 15px;">0</div>
            </div>
            <div class="kpi-card" id="macro-card-FAT Afectada" onclick="filterByMacroCard('FAT Afectada')" style="border-left-color: #f59e0b;">
                <div class="kpi-header-row">
                    <span class="kpi-title">FAT Afectada</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Caja FAT / NAP Afectada</div>
                        <div class="tt-row"><b>Concepto:</b> Falla en splitter o empalme de caja terminal FAT.</div>
                        <div class="tt-formula">Falla_FAT = (ONTs_falla == Total_ONTs) AND (Total_ONTs ≥ 2)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: 100% de ONTs en (OLT, Puerto, FAT) con falla y ≥ 2 abonados.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiFat" style="color: #f59e0b; font-size: 15px;">0</div>
            </div>
            <div class="kpi-card right-align-tooltip" id="macro-card-Falla Cliente" onclick="filterByMacroCard('Falla Cliente')" style="border-left-color: #38bdf8;">
                <div class="kpi-header-row">
                    <span class="kpi-title">Falla Cliente</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">Falla Individual / Cliente (Última Milla)</div>
                        <div class="tt-row"><b>Concepto:</b> ONT con falla cuando no aplica falla masiva de OLT, Clúster, Puerto o FAT.</div>
                        <div class="tt-formula">Falla_Cliente = tiene_falla AND NOT [OLT | Clúster | Puerto | FAT]</div>
                        <div class="tt-row tt-threshold">Umbral: Cualquier anomalía individual en drop, CPE o enlace aislado.</div>
                    </div></span>
                </div>
                <div class="kpi-value" id="kpiCliente" style="color: #38bdf8; font-size: 15px;">0</div>
            </div>
        </div>

        <div class="kpi-grid" id="fila3-cablelabs" style="margin-bottom: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));">
            <div class="kpi-card" id="adv-card-fec" onclick="filterByAdvancedParam('fec')" style="border-left-color: #38bdf8;">
                <div class="kpi-header-row">
                    <span class="kpi-title">1. FEC Crítico</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">1. FEC Crítico / CodeWords</div>
                        <div class="tt-row"><b>Concepto:</b> Palabras de código con error no corregibles o caída de enlace.</div>
                        <div class="tt-formula">is_fec = ("Uncorrectable" IN uncorrect_code_words) OR (ESTADO == "OFFLINE")</div>
                        <div class="tt-row tt-threshold">Umbral de falla: Cualquier palabra "Uncorrectable" o estado OFFLINE.</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: #38bdf8;" id="valFec">0</div>
            </div>
            <div class="kpi-card" id="adv-card-osp" onclick="filterByAdvancedParam('osp')" style="border-left-color: #10b981;">
                <div class="kpi-header-row">
                    <span class="kpi-title">2. Desviación OSP (>3dB)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">2. Desviación OSP (&gt; 3 dB)</div>
                        <div class="tt-row"><b>Concepto:</b> Exceso de pérdida óptica respecto al diseño teórico de referencia (18.0 dB).</div>
                        <div class="tt-formula">ΔL = max(0, |rx_ont| - 18.0) &nbsp;|&nbsp; is_osp = (ΔL ≥ 3.0 dB)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: ΔL ≥ 3.0 dB (equivale a |rx_ont| ≥ 21.0 dBm).</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: #10b981;" id="valOsp">0</div>
            </div>
            <div class="kpi-card" id="adv-card-drop" onclick="filterByAdvancedParam('drop')" style="border-left-color: #f59e0b;">
                <div class="kpi-header-row">
                    <span class="kpi-title">3. Drop Events / Capa 2</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">3. Drop Events / Capa 2 / QoE</div>
                        <div class="tt-row"><b>Concepto:</b> Inestabilidad operacional o baja calidad de experiencia (QoE).</div>
                        <div class="tt-formula">is_drop = (ESTADO IN ["OFFLINE", "STRESSED"]) OR (qoe &lt; 60.0)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: Estado OFFLINE/STRESSED o índice QoE &lt; 60.0.</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: #f59e0b;" id="valDrop">0</div>
            </div>
            <div class="kpi-card" id="adv-card-temp" onclick="filterByAdvancedParam('temp')" style="border-left-color: #a855f7;">
                <div class="kpi-header-row">
                    <span class="kpi-title">4. Estrés Térmico / Clima (ΔT≥10°C)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">4. Estrés Térmico / Clima</div>
                        <div class="tt-row"><b>Concepto:</b> Temperatura del transceptor óptico por encima del umbral nominal (60.0°C).</div>
                        <div class="tt-formula">ΔT = max(0, temp_c - 60.0) &nbsp;|&nbsp; is_temp = (ΔT ≥ 10.0)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: ΔT ≥ 10.0°C (temp_c ≥ 70.0°C) o ΔT ≥ 15.0°C (clima severo).</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: #a855f7;" id="valTemp">0</div>
            </div>
            <div class="kpi-card" id="adv-card-orl" onclick="filterByAdvancedParam('orl')" style="border-left-color: var(--accent-cyan);">
                <div class="kpi-header-row">
                    <span class="kpi-title">5. ORL / Reflectancia (<32dB)</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">5. ORL / Reflectancia Óptica</div>
                        <div class="tt-row"><b>Concepto:</b> Pérdida por retorno óptico estimada a partir de la desviación ΔL.</div>
                        <div class="tt-formula">ORL = 35.0 - (ΔL × 0.8) &nbsp;|&nbsp; is_orl = (ORL &lt; 32.0 dB)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: ORL &lt; 32.0 dB.</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: var(--accent-cyan);" id="valOrl">0</div>
            </div>
            <div class="kpi-card" id="adv-card-sla" onclick="filterByAdvancedParam('sla')" style="border-left-color: #ef4444;">
                <div class="kpi-header-row">
                    <span class="kpi-title">6. Riesgo SLA / Prioridad OT</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">6. Riesgo SLA / Prioridad OT</div>
                        <div class="tt-row"><b>Concepto:</b> ONTs pertenecientes a fallas troncales o zonales de alto impacto.</div>
                        <div class="tt-formula">Riesgo_SLA = macro_hallazgo IN ["Falla OLT", "Falla Clúster", "Puerto (OTDR)"]</div>
                        <div class="tt-row tt-threshold">Umbral de falla: OLT &gt;90%, Clúster &gt;90% o Puerto PON al 100% (≥2 ONTs).</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: #ef4444;" id="valSla">0</div>
            </div>
            <div class="kpi-card right-align-tooltip" id="adv-card-asim" onclick="filterByAdvancedParam('asim')" style="border-left-color: var(--accent-pink);">
                <div class="kpi-header-row">
                    <span class="kpi-title">7. Asimetría Upstream</span>
                    <span class="tooltip-icon">?<div class="tooltip-box">
                        <div class="tt-title">7. Asimetría Upstream</div>
                        <div class="tt-row"><b>Concepto:</b> Desbalance direccional entre recepción ONT (DS) y recepción en OLT (US).</div>
                        <div class="tt-formula">ΔAsim = rx_olt - rx_ont &nbsp;|&nbsp; ΔAttn = (tx_ont - rx_olt) - (3.0 - rx_ont)</div>
                        <div class="tt-row tt-threshold">Umbral de falla: max(|ΔAsim|, |ΔAttn|) ≥ 3.5 dB o (rx_ont ≥ -25.0 dBm y rx_olt &lt; -26.0 dBm).</div>
                    </div></span>
                </div>
                <div class="kpi-value" style="color: var(--accent-pink);" id="valAsim">0</div>
            </div>
        </div>

        <div id="fila4-marcas" class="kpi-grid" style="margin-bottom: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));"></div>

        <div id="summaryWrapper" class="summary-wrapper" style="display: none;">
            <div id="summaryPanel" class="summary-panel">
                <div class="summary-header">
                    <span id="summaryTitle" class="summary-title">RESUMEN JERÁRQUICO</span>
                    <button class="summary-close" onclick="closeSummaryPanel()">Cerrar</button>
                </div>
                <div class="table-container" style="max-height: 280px;">
                    <table id="summaryTable">
                        <thead id="summaryThead"></thead>
                        <tbody id="summaryTbody"></tbody>
                    </table>
                </div>
            </div>
            <div id="summaryPanelSecundario" class="summary-panel">
                <div class="summary-header">
                    <span id="summaryTitleSec" class="summary-title">RESUMEN AGREGADO</span>
                </div>
                <div class="table-container" style="max-height: 280px;">
                    <table id="summaryTableSec">
                        <thead id="summaryTheadSec"></thead>
                        <tbody id="summaryTbodySec"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <div class="table-panel" style="border-top: 3px solid #38bdf8; margin-top: 20px;">
            <div class="table-header">
                <div>
                    <h3 style="color: #38bdf8; margin: 0;">TABLA DE ANÁLISIS - CLARO & VTR</h3>
                </div>
                <div class="pagination-bar">
                    <input type="text" id="globalTableSearchV2" class="global-table-input" placeholder="Filtrar en cualquier columna..." oninput="onTableFilterInput()" />
                    <button type="button" onclick="clearTableFiltersV2()">Limpiar filtros tabla</button>
                    <button class="btn-excel" onclick="exportToExcel('cablelabs')">Exportar CableLabs v2 (.xlsx)</button>
                    <span id="tableCountInfoV2">Mostrando 0-0 de 0</span>
                    <button id="btnPrevV2" onclick="changePageV2(-1)" disabled>&laquo; Anterior</button>
                    <span id="pageIndicatorV2">Página 1 de 1</span>
                    <button id="btnNextV2" onclick="changePageV2(1)" disabled>Siguiente &raquo;</button>
                </div>
            </div>
            <div class="table-container">
                <table id="analysisTableV2">
                    <thead>
                        <tr>
                            <th class="sortable-th" data-sort-key="red" onclick="toggleSortColumn('red')">Red <span class="sort-arrow" id="arrow-red">⇅</span></th>
                            <th class="sortable-th" data-sort-key="serial" onclick="toggleSortColumn('serial')">Serie ONT <span class="sort-arrow" id="arrow-serial">⇅</span></th>
                            <th class="sortable-th" data-sort-key="zona" onclick="toggleSortColumn('zona')">Localidad <span class="sort-arrow" id="arrow-zona">⇅</span></th>
                            <th class="sortable-th" data-sort-key="olt" onclick="toggleSortColumn('olt')">OLT <span class="sort-arrow" id="arrow-olt">⇅</span></th>
                            <th class="sortable-th" data-sort-key="cluster" onclick="toggleSortColumn('cluster')">Cluster <span class="sort-arrow" id="arrow-cluster">⇅</span></th>
                            <th class="sortable-th" data-sort-key="puerto" onclick="toggleSortColumn('puerto')">Puerto PON <span class="sort-arrow" id="arrow-puerto">⇅</span></th>
                            <th class="sortable-th" data-sort-key="fat" onclick="toggleSortColumn('fat')">Caja FAT <span class="sort-arrow" id="arrow-fat">⇅</span></th>
                            <th class="sortable-th" data-sort-key="direccion" onclick="toggleSortColumn('direccion')">Dirección Cliente <span class="sort-arrow" id="arrow-direccion">⇅</span></th>
                            <th class="sortable-th" data-sort-key="macro_hallazgo" onclick="toggleSortColumn('macro_hallazgo')">Hallazgo Red (Macro) <span class="sort-arrow" id="arrow-macro_hallazgo">⇅</span></th>
                            <th class="sortable-th" data-sort-key="tipo_atencion" onclick="toggleSortColumn('tipo_atencion')">Tipo de Atención <span class="sort-arrow" id="arrow-tipo_atencion">⇅</span></th>
                            <th class="sortable-th" data-sort-key="causa_raiz_noc" onclick="toggleSortColumn('causa_raiz_noc')">Causa Raíz NOC <span class="sort-arrow" id="arrow-causa_raiz_noc">⇅</span></th>
                            <th class="sortable-th" data-sort-key="macro_accion_corta" onclick="toggleSortColumn('macro_accion_corta')">Acción Recomendada <span class="sort-arrow" id="arrow-macro_accion_corta">⇅</span></th>
                            <th class="sortable-th" data-sort-key="macro_accion_detallada" onclick="toggleSortColumn('macro_accion_detallada')">Acción Detallada (CableLabs) <span class="sort-arrow" id="arrow-macro_accion_detallada">⇅</span></th>
                            <th class="sortable-th" data-sort-key="sla_priority" onclick="toggleSortColumn('sla_priority')">Prioridad SLA / OT <span class="sort-arrow" id="arrow-sla_priority">⇅</span></th>
                            <th class="sortable-th" data-sort-key="orl_val" onclick="toggleSortColumn('orl_val')">Reflectancia (ORL) <span class="sort-arrow" id="arrow-orl_val">⇅</span></th>
                            <th class="sortable-th" data-sort-key="asimetria_rx_db" onclick="toggleSortColumn('asimetria_rx_db')">Asimetría Subida (dB) <span class="sort-arrow" id="arrow-asimetria_rx_db">⇅</span></th>
                            <th class="sortable-th" data-sort-key="afectacion_asimetria" onclick="toggleSortColumn('afectacion_asimetria')">Afectación Asimetría US <span class="sort-arrow" id="arrow-afectacion_asimetria">⇅</span></th>
                            <th class="sortable-th" data-sort-key="marca_ont" onclick="toggleSortColumn('marca_ont')">Marca ONT <span class="sort-arrow" id="arrow-marca_ont">⇅</span></th>
                            <th class="sortable-th" data-sort-key="condicion" onclick="toggleSortColumn('condicion')">Condición <span class="sort-arrow" id="arrow-condicion">⇅</span></th>
                            <th class="sortable-th" data-sort-key="impacto" onclick="toggleSortColumn('impacto')">Impacto <span class="sort-arrow" id="arrow-impacto">⇅</span></th>
                            <th class="sortable-th" data-sort-key="distancia" onclick="toggleSortColumn('distancia')">Distancia <span class="sort-arrow" id="arrow-distancia">⇅</span></th>
                            <th class="sortable-th" data-sort-key="delta_loss" onclick="toggleSortColumn('delta_loss')">Desfase ΔL <span class="sort-arrow" id="arrow-delta_loss">⇅</span></th>
                            <th>Atenuación OSP</th>
                            <th class="sortable-th" data-sort-key="delta_loss" onclick="toggleSortColumn('delta_loss')">Desviación ΔOSP <span class="sort-arrow" id="arrow-delta_loss_2">⇅</span></th>
                            <th class="sortable-th" data-sort-key="fec_status" onclick="toggleSortColumn('fec_status')">FEC / CodeWords <span class="sort-arrow" id="arrow-fec_status">⇅</span></th>
                            <th class="sortable-th" data-sort-key="delta_temp" onclick="toggleSortColumn('delta_temp')">Estrés Térmico ΔT <span class="sort-arrow" id="arrow-delta_temp">⇅</span></th>
                            <th class="sortable-th" data-sort-key="x" onclick="toggleSortColumn('x')">Rx ONT <span class="sort-arrow" id="arrow-x">⇅</span></th>
                            <th class="sortable-th" data-sort-key="y" onclick="toggleSortColumn('y')">Rx OLT <span class="sort-arrow" id="arrow-y">⇅</span></th>
                            <th class="sortable-th" data-sort-key="tx" onclick="toggleSortColumn('tx')">Tx ONT <span class="sort-arrow" id="arrow-tx">⇅</span></th>
                            <th class="sortable-th" data-sort-key="bias" onclick="toggleSortColumn('bias')">Láser Bias <span class="sort-arrow" id="arrow-bias">⇅</span></th>
                            <th class="sortable-th" data-sort-key="temp" onclick="toggleSortColumn('temp')">Temp (°C) <span class="sort-arrow" id="arrow-temp">⇅</span></th>
                            <th class="sortable-th" data-sort-key="voltage" onclick="toggleSortColumn('voltage')">Voltaje <span class="sort-arrow" id="arrow-voltage">⇅</span></th>
                            <th class="sortable-th" data-sort-key="qoe" onclick="toggleSortColumn('qoe')">QoE <span class="sort-arrow" id="arrow-qoe">⇅</span></th>
                        </tr>
                        <tr class="filter-row">
                            <th><input type="text" class="col-search-input" data-col-key="red" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="serial" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="zona" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="olt" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="cluster" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="puerto" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="fat" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="direccion" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="macro_hallazgo" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="tipo_atencion" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="causa_raiz_noc" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="macro_accion_corta" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="macro_accion_detallada" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="sla_priority" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="orl_val" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="asimetria_rx_db" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="afectacion_asimetria" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="marca_ont" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="condicion" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="impacto" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="distancia" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="delta_loss" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="_osp_base" placeholder="18.5..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="delta_loss" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="fec_status" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="delta_temp" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="x" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="y" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="tx" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="bias" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="temp" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="voltage" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                            <th><input type="text" class="col-search-input" data-col-key="qoe" placeholder="Filtrar..." oninput="onTableFilterInput()" /></th>
                        </tr>
                    </thead>
                    <tbody id="tableBodyV2"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        function showRuntimeError(msg) {
            const el = document.getElementById('runtimeErrorBanner');
            if (el) {
                el.style.display = 'block';
                el.textContent = 'Aviso de ejecución del Dashboard: ' + msg;
            }
        }
        window.addEventListener('error', function(evt) {
            showRuntimeError((evt && evt.message) || 'Error inesperado en script.');
        });

        const __EMBEDDED_POINTS__ = __JSON_MATRIZ_PUNTOS__;
        const rawPoints = (Array.isArray(__EMBEDDED_POINTS__) ? __EMBEDDED_POINTS__ : []).map(p => ({
            red: p.r, x: p.x, y: p.y, tx: p.t, bias: p.b, temp: p.tp, voltage: p.v, qoe: p.q,
            delta_loss: p.dl, fec_status: p.fs, delta_temp: p.dt, orl_val: p.or, sla_priority: p.sp,
            marca_ont: p.mo, causa_raiz_noc: p.cr, macro_accion_corta: p.ac, macro_accion_detallada: p.ad,
            condicion: p.co, impacto: p.im, macro_hallazgo: p.mh, tipo_atencion: p.ta,
            serial: p.sr, zona: p.zo, olt: p.ol, cluster: p.cl, cluster_id: p.cid || '', puerto: p.pu, fat: p.fa,
            direccion: p.di, distancia: p.ds, categoria: p.cat, tiene_falla: p.tf,
            is_fec: p.ifec, is_osp: p.iosp, is_drop: p.idrop, is_temp: p.itemp, is_orl: p.iorl, is_clima: p.icli,
            asimetria_rx_db: p.adbm !== undefined ? p.adbm : 0.0,
            afectacion_asimetria: p.afus || 'NORMAL (0.00 dB)',
            is_asimetria_us: Boolean(p.iasim),
            _osp_base: '18.5 dB'
        }));

        let currentSymptomFilter = 'TODOS';
        let currentMacroFilter = 'TODOS';
        let currentBrandFilter = 'TODOS';
        let currentAdvancedFilter = 'TODOS';
        let currentAttentionFilter = 'TODOS';
        let filterOnlyFailures = false;
        let currentPageV2 = 1;
        const rowsPerPage = 200;
        let activeFilteredPoints = rawPoints;
        let tableViewPoints = rawPoints;
        let sortStateV2 = { key: null, asc: true };
        let parallelBarMode = 'pct';
        const URL_BASE_CLUSTERVIEW_MAP_JS = __JSON_URL_CLUSTERVIEW_MAP__;

        let jerarquiaNavHistory = [];
        let selectedJerarquiaLabel = null;
        let selectedJerarquiaClusterId = '';
        let selectedJerarquiaRed = '';
        let jerarquiaDatasetVisibility = [true, true];

        let chartDonaInstance = null;
        let chartTopJerarquiasInstance = null;
        let chartBarrasApiladasInstance = null;
        let chartOperacionInstance = null;
        let chartCableLabsInstance = null;
        let chartMarcasInstance = null;

        const segmentLabelsPlugin = {
            id: 'segmentLabelsPlugin',
            afterDatasetsDraw(chart) {
                if (chart.config.type !== 'bar' || !chart.options.plugins?.segmentLabels?.enabled) return;
                const { ctx } = chart;
                ctx.save();
                ctx.font = 'bold 11px "Segoe UI", system-ui, sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                chart.data.datasets.forEach((ds, dsIdx) => {
                    const meta = chart.getDatasetMeta(dsIdx);
                    if (!meta || meta.hidden) return;
                    meta.data.forEach((barElem, idx) => {
                        const rawVal = ds.data[idx] || 0;
                        const pctVal = ds._pctData ? ds._pctData[idx] : 0;
                        const minPxWidth = chart.options.plugins.segmentLabels.minWidthPx || 52;
                        const widthPx = Math.abs(barElem.x - barElem.base);
                        if (widthPx < minPxWidth) return;

                        const centerX = (barElem.base + barElem.x) / 2;
                        const centerY = barElem.y;
                        const labelText = parallelBarMode === 'pct'
                            ? `${pctVal.toFixed(1)}%`
                            : `${rawVal.toLocaleString()} (${pctVal.toFixed(0)}%)`;

                        ctx.fillStyle = '#090d16';
                        ctx.fillText(labelText, centerX + 0.8, centerY + 0.8);
                        ctx.fillStyle = '#ffffff';
                        ctx.fillText(labelText, centerX, centerY);
                    });
                });
                ctx.restore();
            }
        };

        if (typeof Chart !== 'undefined' && Chart.register) {
            Chart.register(segmentLabelsPlugin);
        }

        function normalizarClaveRed(redVal) {
            const r = String(redVal || '').trim().toUpperCase();
            if (r.includes('VTR')) return 'VTR';
            if (r.includes('CLARO') || r.includes('CLCH')) return 'CLARO';
            return r;
        }

        function construirUrlClusterView(clusterId, redVal) {
            const cleanId = String(clusterId ?? '').trim();
            if (!cleanId) return '';
            const redKey = normalizarClaveRed(redVal);
            const base = String((URL_BASE_CLUSTERVIEW_MAP_JS && URL_BASE_CLUSTERVIEW_MAP_JS[redKey]) || '').trim();
            if (!base) return '';
            const normalizedBase = base.endsWith('/') ? base : `${base}/`;
            return `${normalizedBase}${encodeURIComponent(cleanId)}`;
        }

        function resolverContextoClusterActivo(execData) {
            const fuente = Array.isArray(execData) && execData.length ? execData : rawPoints;

            if (selectedJerarquiaClusterId && String(selectedJerarquiaClusterId).trim() !== '') {
                let redResuelta = selectedJerarquiaRed;
                if (!redResuelta && selectedJerarquiaLabel) {
                    const m = fuente.find(p => `${p.red}-${p.olt}-${p.cluster}` === selectedJerarquiaLabel);
                    if (m) redResuelta = m.red;
                }
                if (!redResuelta) {
                    const fRed = document.getElementById('filtroRed')?.value;
                    if (fRed && fRed !== 'TODAS') redResuelta = fRed;
                }
                return {
                    clusterId: String(selectedJerarquiaClusterId).trim(),
                    red: normalizarClaveRed(redResuelta)
                };
            }

            const filtroClusterVal = document.getElementById('filtroCluster')?.value;
            const filtroRedVal = document.getElementById('filtroRed')?.value;

            if (filtroClusterVal && filtroClusterVal !== 'TODOS') {
                const match = fuente.find(p =>
                    p.cluster === filtroClusterVal &&
                    String(p.cluster_id || '').trim() !== '' &&
                    (!filtroRedVal || filtroRedVal === 'TODAS' || normalizarClaveRed(p.red) === normalizarClaveRed(filtroRedVal))
                ) || rawPoints.find(p =>
                    p.cluster === filtroClusterVal &&
                    String(p.cluster_id || '').trim() !== ''
                );
                if (match) {
                    return {
                        clusterId: String(match.cluster_id).trim(),
                        red: normalizarClaveRed(match.red)
                    };
                }
            }

            if (selectedJerarquiaLabel) {
                const found = fuente.find(p =>
                    `${p.red}-${p.olt}-${p.cluster}` === selectedJerarquiaLabel &&
                    String(p.cluster_id || '').trim() !== ''
                );
                if (found) {
                    return {
                        clusterId: String(found.cluster_id).trim(),
                        red: normalizarClaveRed(found.red)
                    };
                }
            }

            return { clusterId: '', red: '' };
        }

        function openSelectedClusterInTraxium() {
            const ctx = resolverContextoClusterActivo(activeFilteredPoints);
            const url = construirUrlClusterView(ctx.clusterId, ctx.red);
            if (!url) return;
            window.open(url, '_blank', 'noopener,noreferrer');
        }

        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById('btn-tab-' + tabName).classList.add('active');
            document.getElementById('tab-' + tabName).classList.add('active');
            if (tabName === 'gerencial') {
                updateExecutiveDashboard();
            }
        }

        function setParallelBarMode(mode) {
            parallelBarMode = mode;
            document.getElementById('btnModePct').classList.toggle('active', mode === 'pct');
            document.getElementById('btnModeAbs').classList.toggle('active', mode === 'abs');
            updateExecutiveDashboard();
        }

        function initDropdowns() {
            populateDropdown('filtroRed', 'red', '-- Todas las Redes --', 'TODAS');
            populateDropdown('filtroLocalidad', 'zona', '-- Todas las Localidades --', 'TODAS');
            populateDropdown('filtroOlt', 'olt', '-- Todas las OLTs --', 'TODAS');
            populateDropdown('filtroCluster', 'cluster', '-- Todos los Clusters --', 'TODOS');
            populateDropdown('filtroPuerto', 'puerto', '-- Todos los Puertos --', 'TODOS');
        }

        function populateDropdown(elementId, fieldName, defaultText, defaultValue) {
            const select = document.getElementById(elementId);
            const currentVal = select.value;

            const partialFiltered = rawPoints.filter(p => {
                if (elementId !== 'filtroRed') {
                    const val = document.getElementById('filtroRed').value;
                    if (val !== 'TODAS' && p.red !== val) return false;
                }
                if (elementId !== 'filtroLocalidad') {
                    const val = document.getElementById('filtroLocalidad').value;
                    if (val !== 'TODAS' && p.zona !== val) return false;
                }
                if (elementId !== 'filtroOlt') {
                    const val = document.getElementById('filtroOlt').value;
                    if (val !== 'TODAS' && p.olt !== val) return false;
                }
                if (elementId !== 'filtroCluster') {
                    const val = document.getElementById('filtroCluster').value;
                    if (val !== 'TODOS' && p.cluster !== val) return false;
                }
                if (elementId !== 'filtroPuerto') {
                    const val = document.getElementById('filtroPuerto').value;
                    if (val !== 'TODOS' && p.puerto !== val) return false;
                }
                if (elementId !== 'filtroCondicion') {
                    const val = document.getElementById('filtroCondicion').value;
                    if (val !== 'TODAS' && p.condicion !== val) return false;
                }
                if (elementId !== 'filtroImpacto') {
                    const val = document.getElementById('filtroImpacto').value;
                    if (val !== 'TODOS' && p.impacto !== val) return false;
                }
                return true;
            });

            const uniqueValues = [...new Set(partialFiltered.map(p => p[fieldName]))].sort();

            select.innerHTML = `<option value="${defaultValue}">${defaultText}</option>`;
            uniqueValues.forEach(val => {
                const opt = document.createElement('option');
                opt.value = val;
                opt.textContent = val;
                if (val === currentVal) opt.selected = true;
                select.appendChild(opt);
            });
            if (uniqueValues.includes(currentVal)) {
                select.value = currentVal;
            } else {
                select.value = defaultValue;
            }
        }

        function updateActiveCardHighlights() {
            document.querySelectorAll('.kpi-card').forEach(c => c.classList.remove('active-filter'));
            if (filterOnlyFailures) document.getElementById('card-FAVOR')?.classList.add('active-filter');
            if (currentSymptomFilter !== 'TODOS') document.getElementById('card-' + currentSymptomFilter)?.classList.add('active-filter');
            if (currentAttentionFilter === 'Correctiva (Inmediata)') document.getElementById('card-Correctiva')?.classList.add('active-filter');
            if (currentAttentionFilter === 'Preventiva (Proactiva de Red)') document.getElementById('card-Preventiva')?.classList.add('active-filter');
            if (currentMacroFilter !== 'TODOS') document.getElementById('macro-card-' + currentMacroFilter)?.classList.add('active-filter');
            if (currentAdvancedFilter !== 'TODOS') document.getElementById('adv-card-' + currentAdvancedFilter)?.classList.add('active-filter');
            if (!filterOnlyFailures && currentSymptomFilter === 'TODOS' && currentAttentionFilter === 'TODOS' && currentMacroFilter === 'TODOS' && currentAdvancedFilter === 'TODOS' && currentBrandFilter === 'TODOS') {
                document.getElementById('card-TODOS')?.classList.add('active-filter');
            }
        }

        function renderBrandKpiCards(dataPoints) {
            const container = document.getElementById('fila4-marcas');
            container.innerHTML = '';
            const brandCounts = {};
            dataPoints.forEach(p => { const b = p.marca_ont || 'Desconocida'; brandCounts[b] = (brandCounts[b] || 0) + 1; });
            const sortedBrands = Object.keys(brandCounts).sort((a, b) => brandCounts[b] - brandCounts[a]);
            const colors = ['#ef4444', '#38bdf8', '#10b981', '#f59e0b', '#a855f7', '#ec4899'];

            sortedBrands.forEach((b, idx) => {
                const safeId = b.replace(/[^a-zA-Z0-9]/g, '');
                const col = colors[idx % colors.length];
                const isActive = currentBrandFilter === b ? ' active-filter' : '';
                const rightClass = (idx >= sortedBrands.length - 2) ? ' right-align-tooltip' : '';
                container.innerHTML += `
                    <div class="kpi-card${isActive}${rightClass}" id="brand-card-${safeId}" onclick="filterByBrand('${b}')" style="border-left-color: ${col};">
                        <div class="kpi-header-row"><span class="kpi-title">${b}</span><span class="tooltip-icon">?<div class="tooltip-box"><div class="tt-title">Fabricante ${b}</div><div class="tt-row"><b>Concepto:</b> Equipos CPE asociados a la marca <b>${b}</b>.</div><div class="tt-formula">Marca = MATCH(marca_desc / SERIE ONT)</div><div class="tt-row tt-threshold">Umbral: Clasificación por prefijo/vendor en telemetría o serie ONT.</div></div></span></div>
                        <div class="kpi-value" style="color: ${col};">${brandCounts[b].toLocaleString()}</div>
                    </div>
                `;
            });
        }

        function updateKpiCards() {
            const baseForCards = activeFilteredPoints;
            const tot = baseForCards.length;
            const failCount = baseForCards.filter(p => p.tiene_falla).length;
            const opt = baseForCards.filter(p => p.categoria === '1. Rango Nominal').length;
            const correctivaCnt = baseForCards.filter(p => p.tipo_atencion === 'Correctiva (Inmediata)').length;
            const preventivaCnt = baseForCards.filter(p => p.tipo_atencion === 'Preventiva (Proactiva de Red)').length;
            const las = baseForCards.filter(p => p.categoria === '4. Vector Bias (> 35mA)').length;

            document.getElementById('kpiTotal').innerText = tot.toLocaleString();
            document.getElementById('kpiFallas').innerText = failCount.toLocaleString();
            document.getElementById('kpiOptimo').innerText = opt.toLocaleString();
            document.getElementById('kpiCorrectiva').innerText = correctivaCnt.toLocaleString();
            document.getElementById('kpiPreventiva').innerText = preventivaCnt.toLocaleString();
            document.getElementById('kpiLaser').innerText = las.toLocaleString();

            const oltOnes = baseForCards.filter(p => p.macro_hallazgo === 'Falla OLT');
            const numOlts = new Set(oltOnes.map(p => p.olt)).size;
            document.getElementById('kpiOlt').innerText = `ONTs ${oltOnes.length.toLocaleString()} / OLTs ${numOlts.toLocaleString()}`;

            const cluOnes = baseForCards.filter(p => p.macro_hallazgo === 'Falla Clúster');
            const numClus = new Set(cluOnes.map(p => p.cluster)).size;
            document.getElementById('kpiCluster').innerText = `ONTs ${cluOnes.length.toLocaleString()} / CLUSTER ${numClus.toLocaleString()}`;

            const pueOnes = baseForCards.filter(p => p.macro_hallazgo === 'Puerto (OTDR)');
            const numPues = new Set(pueOnes.map(p => p.puerto)).size;
            document.getElementById('kpiPuerto').innerText = `ONTs ${pueOnes.length.toLocaleString()} / PUERTOS ${numPues.toLocaleString()}`;

            const fatOnes = baseForCards.filter(p => p.macro_hallazgo === 'FAT Afectada');
            const numFats = new Set(fatOnes.map(p => `${p.olt}_${p.puerto}_${p.fat}`)).size;
            document.getElementById('kpiFat').innerText = `ONTs ${fatOnes.length.toLocaleString()} / FATs ${numFats.toLocaleString()}`;

            const cliOnes = baseForCards.filter(p => p.macro_hallazgo === 'Falla Cliente');
            document.getElementById('kpiCliente').innerText = `CLIENTES ${cliOnes.length.toLocaleString()}`;

            document.getElementById('valFec').innerText = baseForCards.filter(p => p.is_fec).length.toLocaleString();
            document.getElementById('valOsp').innerText = baseForCards.filter(p => p.is_osp).length.toLocaleString();
            document.getElementById('valDrop').innerText = baseForCards.filter(p => p.is_drop).length.toLocaleString();
            document.getElementById('valTemp').innerText = baseForCards.filter(p => p.is_temp || p.is_clima).length.toLocaleString();
            document.getElementById('valOrl').innerText = baseForCards.filter(p => p.is_orl).length.toLocaleString();
            document.getElementById('valSla').innerText = baseForCards.filter(p => ['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)'].includes(p.macro_hallazgo)).length.toLocaleString();
            document.getElementById('valAsim').innerText = baseForCards.filter(p => p.is_asimetria_us).length.toLocaleString();

            renderBrandKpiCards(baseForCards);
            updateActiveCardHighlights();
        }

        function clasificarTipoFalla(p) {
            if (p.categoria === '4. Vector Bias (> 35mA)') return 'Vector Bias (>35mA)';
            if (p.categoria === '3. Falla Asimétrica Upstream' || p.is_asimetria_us) return 'Asimetría Upstream';
            if (p.is_fec) return 'FEC Crítico / Offline';
            if (p.is_osp || p.is_orl) return 'Atenuación OSP / ORL';
            if (p.is_drop) return 'Drop / Capa 2 / QoE';
            if (p.is_temp || p.is_clima) return 'Estrés Térmico / Clima';
            return 'Degradación ODN';
        }

        function updateExecutiveDashboard() {
            const red = document.getElementById('filtroRed').value;
            const zona = document.getElementById('filtroLocalidad').value;
            const olt = document.getElementById('filtroOlt').value;
            const cluster = document.getElementById('filtroCluster').value;
            const puerto = document.getElementById('filtroPuerto').value;
            const condicion = document.getElementById('filtroCondicion').value;
            const impacto = document.getElementById('filtroImpacto').value;

            const execData = rawPoints.filter(p => {
                if (red !== 'TODAS' && p.red !== red) return false;
                if (zona !== 'TODAS' && p.zona !== zona) return false;
                if (olt !== 'TODAS' && p.olt !== olt) return false;
                if (cluster !== 'TODOS' && p.cluster !== cluster) return false;
                if (puerto !== 'TODOS' && p.puerto !== puerto) return false;
                if (condicion !== 'TODAS' && p.condicion !== condicion) return false;
                if (impacto !== 'TODOS' && p.impacto !== impacto) return false;
                return true;
            });

            const total = execData.length;
            const nominales = execData.filter(p => !p.tiene_falla).length;
            const fallas = execData.filter(p => p.tiene_falla);
            const totalFallas = fallas.length;

            const redMacros = ['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)', 'FAT Afectada'];
            const fallasRed = fallas.filter(p => redMacros.includes(p.macro_hallazgo));
            const fallasUnitarias = fallas.filter(p => p.macro_hallazgo === 'Falla Cliente');

            const saludPct = total > 0 ? ((nominales / total) * 100).toFixed(1) : '0.0';
            const problemasCnt = Math.max(0, total - nominales);
            const problemasPct = total > 0 ? ((problemasCnt / total) * 100).toFixed(1) : '0.0';
            const redPct = totalFallas > 0 ? ((fallasRed.length / totalFallas) * 100).toFixed(1) : '0.0';
            const unitPct = totalFallas > 0 ? ((fallasUnitarias.length / totalFallas) * 100).toFixed(1) : '0.0';

            document.getElementById('execProblemasPct').innerText = `${problemasPct}%`;
            document.getElementById('execProblemasDesc').innerText = `${problemasCnt.toLocaleString()} de ${total.toLocaleString()} ONTs operando con problemas.`;
            document.getElementById('execSaludPct').innerText = `${saludPct}%`;
            document.getElementById('execSaludDesc').innerText = `${nominales.toLocaleString()} de ${total.toLocaleString()} ONTs operando en rango nominal CableLabs.`;
            document.getElementById('execRedPct').innerText = `${fallasRed.length.toLocaleString()} (${redPct}%)`;
            document.getElementById('execRedDesc').innerText = `ONTs afectadas por jerarquía de red (OLT, Clúster, Puerto PON o FAT) sobre el total con fallas.`;
            document.getElementById('execUnitPct').innerText = `${fallasUnitarias.length.toLocaleString()} (${unitPct}%)`;
            document.getElementById('execUnitDesc').innerText = `ONTs con fallas individuales de última milla / equipo abonado.`;

            const uniqueOltF = new Set(fallasRed.filter(p => p.macro_hallazgo === 'Falla OLT').map(p => `${p.red}_${p.olt}`)).size;
            const uniqueCluF = new Set(fallasRed.filter(p => p.macro_hallazgo === 'Falla Clúster').map(p => `${p.red}_${p.cluster}`)).size;
            const uniquePueF = new Set(fallasRed.filter(p => p.macro_hallazgo === 'Puerto (OTDR)').map(p => `${p.red}_${p.puerto}`)).size;
            const uniqueFatF = new Set(fallasRed.filter(p => p.macro_hallazgo === 'FAT Afectada').map(p => `${p.red}_${p.olt}_${p.puerto}_${p.fat}`)).size;
            const totalElementosRed = uniqueOltF + uniqueCluF + uniquePueF + uniqueFatF;
            const ratioConsolidacion = totalElementosRed > 0 ? (fallasRed.length / totalElementosRed).toFixed(1) : '0.0';

            document.getElementById('execRatioOt').innerText = `${ratioConsolidacion} ONTs/OT`;
            document.getElementById('execRatioDesc').innerText = `${totalElementosRed.toLocaleString()} puntos de red (${uniqueOltF} OLT, ${uniqueCluF} Clúster, ${uniquePueF} Puertos, ${uniqueFatF} FAT) agrupan ${fallasRed.length.toLocaleString()} clientes.`;

            const cntOlt = fallasRed.filter(p => p.macro_hallazgo === 'Falla OLT').length;
            const cntClu = fallasRed.filter(p => p.macro_hallazgo === 'Falla Clúster').length;
            const cntPue = fallasRed.filter(p => p.macro_hallazgo === 'Puerto (OTDR)').length;
            const cntFat = fallasRed.filter(p => p.macro_hallazgo === 'FAT Afectada').length;

            const tiposGlobalMap = {};
            fallas.forEach(p => {
                const tf = clasificarTipoFalla(p);
                tiposGlobalMap[tf] = (tiposGlobalMap[tf] || 0) + 1;
            });

            let unitBias = 0, unitAsim = 0, unitOspDrop = 0, unitTermico = 0, unitOtros = 0;
            fallasUnitarias.forEach(p => {
                if (p.categoria === '4. Vector Bias (> 35mA)') unitBias++;
                else if (p.categoria === '3. Falla Asimétrica Upstream' || p.is_asimetria_us) unitAsim++;
                else if (p.is_osp || p.is_orl || p.is_drop || p.is_fec) unitOspDrop++;
                else if (p.is_temp || p.is_clima) unitTermico++;
                else unitOtros++;
            });

            const unitTermicoTotal = (unitTermico || 0) + (unitOtros || 0);

            renderDonaOpcionC(
                fallasRed.length,
                fallasUnitarias.length,
                { cntOlt, cntClu, cntPue, cntFat },
                { unitBias, unitAsim, unitOspDrop, unitTermicoTotal },
                tiposGlobalMap
            );

            renderParallelStackedBars(
                fallasRed.length,
                fallasUnitarias.length,
                { cntOlt, cntClu, cntPue, cntFat },
                { unitBias, unitAsim, unitOspDrop, unitTermicoTotal }
            );

            renderTopJerarquiasChart(execData);
            renderOperacionAtencionChart(execData);
            renderCableLabsExecChart(execData);
            renderMarcasExecChart(execData);
            renderExecPriorityTable(execData);
        }

        function renderDonaOpcionC(totalRed, totalUnit, redSub, unitSub, tiposGlobalMap) {
            const ctx = document.getElementById('chartDonaMultinivel').getContext('2d');
            if (chartDonaInstance) chartDonaInstance.destroy();

            const totFallas = totalRed + totalUnit;
            document.getElementById('donaCenterTotal').innerText = totFallas.toLocaleString();

            const ring2Labels = [
                'Red: Falla OLT (>90%)',
                'Red: Falla Clúster (>90%)',
                'Red: Puerto PON (100%)',
                'Red: Caja FAT (100%)',
                'Unitaria: Vector Bias (>35mA)',
                'Unitaria: Asimetría Upstream',
                'Unitaria: Atenuación ODN / Drop / ORL',
                'Unitaria: Estrés Térmico / Clima'
            ];
            const ring2Data = [
                redSub.cntOlt, redSub.cntClu, redSub.cntPue, redSub.cntFat,
                unitSub.unitBias, unitSub.unitAsim, unitSub.unitOspDrop, unitSub.unitTermicoTotal
            ];
            const ring2Colors = [
                '#ef4444', '#f97316', '#fb7185', '#f59e0b',
                '#a855f7', '#06b6d4', '#38bdf8', '#10b981'
            ];

            const ring1Labels = ['Fallas de Red (OSP)', 'Fallas Unitarias (Cliente)'];
            const ring1Data = [totalRed, totalUnit];
            const ring1Colors = ['#dc2626', '#2563eb'];

            chartDonaInstance = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ring2Labels,
                    datasets: [
                        {
                            label: 'Desglose (Jerarquía / Categoría)',
                            data: ring2Data,
                            backgroundColor: ring2Colors,
                            borderColor: '#0f172a',
                            borderWidth: 2,
                            hoverOffset: 5,
                            weight: 0.85
                        },
                        {
                            label: 'Origen (Anillo Interior Grueso)',
                            data: ring1Data,
                            backgroundColor: ring1Colors,
                            borderColor: '#0f172a',
                            borderWidth: 3,
                            hoverOffset: 4,
                            weight: 1.55
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '34%',
                    layout: { padding: 8 },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            enabled: true,
                            backgroundColor: 'rgba(9, 13, 22, 0.97)',
                            titleColor: '#38bdf8',
                            bodyColor: '#f8fafc',
                            borderColor: '#38bdf8',
                            borderWidth: 1,
                            padding: 10,
                            callbacks: {
                                label: function(context) {
                                    const dsIndex = context.datasetIndex;
                                    const idx = context.dataIndex;
                                    const val = context.raw || 0;
                                    const sum = context.dataset.data.reduce((a, b) => a + b, 0);
                                    const pct = sum > 0 ? ((val / sum) * 100).toFixed(1) : 0;
                                    const lbl = dsIndex === 1 ? ring1Labels[idx] : ring2Labels[idx];
                                    return ` ${lbl}: ${val.toLocaleString()} ONTs (${pct}%)`;
                                }
                            }
                        }
                    },
                    onClick: (e, elements) => {
                        if (!elements.length) return;
                        const el = elements[0];
                        if (el.datasetIndex === 0) {
                            const mapMacro = ['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)', 'FAT Afectada'];
                            switchTab('operativo');
                            if (el.index < 4) renderSummaryTable(mapMacro[el.index]);
                            else renderSummaryTable('Falla Cliente');
                        } else if (el.datasetIndex === 1) {
                            switchTab('operativo');
                            if (el.index === 0) filterByAdvancedParam('sla');
                            else renderSummaryTable('Falla Cliente');
                        }
                    },
                    onHover: (event, elements) => {
                        const overlay = document.getElementById('donutCenterOverlay');
                        if (!overlay) return;
                        if (elements && elements.length > 0) overlay.classList.add('below-tooltip');
                        else overlay.classList.remove('below-tooltip');
                    }
                }
            });

            const pctRed = totFallas > 0 ? ((totalRed / totFallas) * 100).toFixed(1) : '0.0';
            const pctUnit = totFallas > 0 ? ((totalUnit / totFallas) * 100).toFixed(1) : '0.0';

            const faultPalette = {
                'Atenuación OSP / ORL': '#38bdf8',
                'Asimetría Upstream': '#06b6d4',
                'Estrés Térmico / Clima': '#10b981',
                'FEC Crítico / Offline': '#ef4444',
                'Drop / Capa 2 / QoE': '#f59e0b',
                'Vector Bias (>35mA)': '#a855f7',
                'Degradación ODN': '#94a3b8'
            };

            const sortedTipos = Object.entries(tiposGlobalMap).sort((a, b) => b[1] - a[1]);
            let barsHtml = '';
            sortedTipos.forEach(([tName, cnt]) => {
                const pct = totFallas > 0 ? ((cnt / totFallas) * 100).toFixed(1) : '0.0';
                const barWidth = totFallas > 0 ? Math.max(2, (cnt / totFallas) * 100) : 0;
                const color = faultPalette[tName] || '#38bdf8';
                barsHtml += `
                    <div class="fault-bar-row">
                        <div class="fault-bar-header">
                            <span><b>${tName}</b></span>
                            <span class="fault-bar-meta">${cnt.toLocaleString()} (${pct}%)</span>
                        </div>
                        <div class="fault-bar-track">
                            <div class="fault-bar-fill" style="width: ${barWidth}%; background: ${color};"></div>
                        </div>
                    </div>
                `;
            });

            document.getElementById('donaBreakdownPanel').innerHTML = `
                <div>
                    <div class="breakdown-section-title" style="color: #f8fafc;">
                        <span>Origen y Jerarquía</span>
                        <span style="color: #94a3b8;">${totFallas.toLocaleString()}</span>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); filterByAdvancedParam('sla');">
                        <span><span class="legend-dot" style="background:#dc2626;"></span><b>Red (OSP)</b></span>
                        <b style="color:#ef4444;">${totalRed.toLocaleString()} (${pctRed}%)</b>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); renderSummaryTable('Falla OLT');" style="padding-left:14px; font-size:10.5px;">
                        <span><span class="legend-dot" style="background:#ef4444;"></span>Falla OLT (>90%)</span>
                        <span>${redSub.cntOlt.toLocaleString()}</span>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); renderSummaryTable('Falla Clúster');" style="padding-left:14px; font-size:10.5px;">
                        <span><span class="legend-dot" style="background:#f97316;"></span>Falla Clúster (>90%)</span>
                        <span>${redSub.cntClu.toLocaleString()}</span>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); renderSummaryTable('Puerto (OTDR)');" style="padding-left:14px; font-size:10.5px;">
                        <span><span class="legend-dot" style="background:#fb7185;"></span>Puerto PON (100%)</span>
                        <span>${redSub.cntPue.toLocaleString()}</span>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); renderSummaryTable('FAT Afectada');" style="padding-left:14px; font-size:10.5px;">
                        <span><span class="legend-dot" style="background:#f59e0b;"></span>Caja FAT (100%)</span>
                        <span>${redSub.cntFat.toLocaleString()}</span>
                    </div>
                    <div class="legend-item" onclick="switchTab('operativo'); renderSummaryTable('Falla Cliente');" style="margin-top:4px;">
                        <span><span class="legend-dot" style="background:#2563eb;"></span><b>Unitarias (Cliente)</b></span>
                        <b style="color:#38bdf8;">${totalUnit.toLocaleString()} (${pctUnit}%)</b>
                    </div>
                </div>
                <div>
                    <div class="breakdown-section-title" style="color: #38bdf8;">
                        <span>Desglose por Tipo de Falla</span>
                        <span style="color: #94a3b8;">% Total</span>
                    </div>
                    ${barsHtml || '<div class="fault-bar-meta">Sin fallas registradas para el filtro actual.</div>'}
                </div>
            `;
        }

        function renderParallelStackedBars(totalRed, totalUnit, redSub, unitSub) {
            const ctx = document.getElementById('chartBarrasApiladasParalelo').getContext('2d');
            if (chartBarrasApiladasInstance) chartBarrasApiladasInstance.destroy();

            const subSpecs = [
                { key: 'olt', label: 'Falla OLT (>90%)', color: '#ef4444', redVal: redSub.cntOlt, unitVal: 0 },
                { key: 'clu', label: 'Falla Clúster (>90%)', color: '#f97316', redVal: redSub.cntClu, unitVal: 0 },
                { key: 'pue', label: 'Puerto PON (100%)', color: '#fb7185', redVal: redSub.cntPue, unitVal: 0 },
                { key: 'fat', label: 'Caja FAT (100%)', color: '#f59e0b', redVal: redSub.cntFat, unitVal: 0 },
                { key: 'bias', label: 'Vector Bias (>35mA)', color: '#a855f7', redVal: 0, unitVal: unitSub.unitBias },
                { key: 'asim', label: 'Asimetría Upstream', color: '#06b6d4', redVal: 0, unitVal: unitSub.unitAsim },
                { key: 'odn', label: 'Atenuación ODN / Drop / ORL', color: '#38bdf8', redVal: 0, unitVal: unitSub.unitOspDrop },
                { key: 'term', label: 'Estrés Térmico / Clima', color: '#10b981', redVal: 0, unitVal: unitSub.unitTermicoTotal }
            ];

            const pctRed = s => totalRed > 0 ? (s.redVal / totalRed) * 100 : 0;
            const pctUnit = s => totalUnit > 0 ? (s.unitVal / totalUnit) * 100 : 0;

            const labels = [
                `Fallas de Red (OSP)\n${totalRed.toLocaleString()} ONTs`,
                `Fallas Unitarias (Cliente)\n${totalUnit.toLocaleString()} ONTs`
            ];

            const datasets = subSpecs.map(s => {
                const absArr = [s.redVal, s.unitVal];
                const pctArr = [pctRed(s), pctUnit(s)];
                return {
                    label: s.label,
                    data: parallelBarMode === 'pct' ? pctArr : absArr,
                    _absData: absArr,
                    _pctData: pctArr,
                    backgroundColor: s.color,
                    borderColor: '#090d16',
                    borderWidth: 2,
                    borderRadius: 4,
                    barThickness: 44
                };
            });

            chartBarrasApiladasInstance = new Chart(ctx, {
                type: 'bar',
                data: { labels, datasets },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            stacked: true,
                            max: parallelBarMode === 'pct' ? 100 : undefined,
                            grid: { color: '#1e293b' },
                            ticks: {
                                color: '#cbd5e1',
                                font: { size: 11 },
                                callback: v => parallelBarMode === 'pct' ? `${v}%` : Number(v).toLocaleString()
                            }
                        },
                        y: {
                            stacked: true,
                            grid: { display: false },
                            ticks: { color: '#f8fafc', font: { size: 12, weight: 'bold' } }
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        segmentLabels: {
                            enabled: true,
                            minWidthPx: parallelBarMode === 'pct' ? 54 : 74
                        },
                        tooltip: {
                            enabled: true,
                            backgroundColor: 'rgba(9, 13, 22, 0.97)',
                            titleColor: '#38bdf8',
                            bodyColor: '#f8fafc',
                            borderColor: '#38bdf8',
                            borderWidth: 1,
                            padding: 10,
                            callbacks: {
                                label: function(context) {
                                    const ds = context.dataset;
                                    const idx = context.dataIndex;
                                    const absVal = ds._absData[idx] || 0;
                                    const pctVal = ds._pctData[idx] || 0;
                                    return ` ${ds.label}: ${absVal.toLocaleString()} ONTs (${pctVal.toFixed(1)}% de la barra)`;
                                }
                            }
                        }
                    }
                }
            });

            document.getElementById('parallelStackedLegend').innerHTML = subSpecs.map(s => `
                <span class="parallel-legend-item">
                    <span class="legend-dot" style="background:${s.color};"></span>
                    <span>${s.label}</span>
                </span>
            `).join('');

            const redChips = subSpecs.filter(s => s.redVal > 0).map(s => `
                <span class="parallel-chip">
                    <span class="legend-dot" style="background:${s.color}; margin-right:0;"></span>
                    <span>${s.label}: <b>${s.redVal.toLocaleString()}</b> (${pctRed(s).toFixed(1)}%)</span>
                </span>
            `).join('') || '<span class="fault-bar-meta">Sin subcategorías de red activas.</span>';

            const unitChips = subSpecs.filter(s => s.unitVal > 0).map(s => `
                <span class="parallel-chip">
                    <span class="legend-dot" style="background:${s.color}; margin-right:0;"></span>
                    <span>${s.label}: <b>${s.unitVal.toLocaleString()}</b> (${pctUnit(s).toFixed(1)}%)</span>
                </span>
            `).join('') || '<span class="fault-bar-meta">Sin subcategorías unitarias activas.</span>';

            document.getElementById('parallelSummaryStrip').innerHTML = `
                <div class="parallel-category-card" style="border-left: 3px solid #dc2626;">
                    <div class="parallel-category-header">
                        <span style="color:#f87171;">Barra 1: Fallas de Red (OSP)</span>
                        <span>${totalRed.toLocaleString()} ONTs</span>
                    </div>
                    <div class="parallel-chips-wrap">${redChips}</div>
                </div>
                <div class="parallel-category-card" style="border-left: 3px solid #2563eb;">
                    <div class="parallel-category-header">
                        <span style="color:#60a5fa;">Barra 2: Fallas Unitarias (Cliente)</span>
                        <span>${totalUnit.toLocaleString()} ONTs</span>
                    </div>
                    <div class="parallel-chips-wrap">${unitChips}</div>
                </div>
            `;
        }

        function isChartDatasetVisible(chart, idx) {
            if (!chart) return jerarquiaDatasetVisibility[idx] !== false;
            const meta = chart.getDatasetMeta(idx);
            if (!meta || meta.hidden === null || meta.hidden === undefined) {
                return !chart.data.datasets[idx]?.hidden;
            }
            return !meta.hidden;
        }

        function captureCurrentFilterSnapshot() {
            return {
                red: document.getElementById('filtroRed').value,
                zona: document.getElementById('filtroLocalidad').value,
                olt: document.getElementById('filtroOlt').value,
                cluster: document.getElementById('filtroCluster').value,
                puerto: document.getElementById('filtroPuerto').value,
                condicion: document.getElementById('filtroCondicion').value,
                impacto: document.getElementById('filtroImpacto').value,
                selectedLabel: selectedJerarquiaLabel,
                selectedClusterId: selectedJerarquiaClusterId,
                selectedRed: selectedJerarquiaRed,
                datasetVisibility: [...jerarquiaDatasetVisibility]
            };
        }

        function restoreFilterSnapshot(snap) {
            if (!snap) return;
            if (Array.isArray(snap.datasetVisibility) && snap.datasetVisibility.length === 2) {
                jerarquiaDatasetVisibility = [Boolean(snap.datasetVisibility[0]), Boolean(snap.datasetVisibility[1])];
            }

            document.getElementById('filtroRed').value = snap.red || 'TODAS';
            document.getElementById('filtroLocalidad').value = snap.zona || 'TODAS';
            document.getElementById('filtroCondicion').value = snap.condicion || 'TODAS';
            document.getElementById('filtroImpacto').value = snap.impacto || 'TODOS';

            initDropdowns();
            if (snap.olt && Array.from(document.getElementById('filtroOlt').options).some(o => o.value === snap.olt)) {
                document.getElementById('filtroOlt').value = snap.olt;
            }
            initDropdowns();
            if (snap.cluster && Array.from(document.getElementById('filtroCluster').options).some(o => o.value === snap.cluster)) {
                document.getElementById('filtroCluster').value = snap.cluster;
            }
            initDropdowns();
            if (snap.puerto && Array.from(document.getElementById('filtroPuerto').options).some(o => o.value === snap.puerto)) {
                document.getElementById('filtroPuerto').value = snap.puerto;
            }

            selectedJerarquiaLabel = snap.selectedLabel || null;
            selectedJerarquiaClusterId = snap.selectedClusterId || '';
            selectedJerarquiaRed = snap.selectedRed || '';
            onFilterChanged();
        }

        function updateJerarquiaNavigationUI(execData) {
            const backBtn = document.getElementById('btnJerarquiaBack');
            const traxiumBtn = document.getElementById('btnAbrirTraxiumCluster');
            const crumbBar = document.getElementById('jerarquiasBreadcrumb');

            const hasHistory = jerarquiaNavHistory.length > 0;
            const hasSelection = Boolean(selectedJerarquiaLabel);
            const activeCtx = resolverContextoClusterActivo(execData);
            const clusterUrl = construirUrlClusterView(activeCtx.clusterId, activeCtx.red);
            const hasClusterUrl = Boolean(clusterUrl);

            if (backBtn) backBtn.style.display = (hasHistory || hasSelection) ? 'inline-flex' : 'none';
            if (traxiumBtn) {
                traxiumBtn.style.display = hasClusterUrl ? 'inline-flex' : 'none';
                if (hasClusterUrl) {
                    traxiumBtn.title = `Abrir ${activeCtx.red} clúster ${activeCtx.clusterId} en Traxium (${clusterUrl})`;
                }
            }

            if (!crumbBar) return;
            if (!hasHistory && !hasSelection && !hasClusterUrl) {
                crumbBar.style.display = 'none';
                crumbBar.innerHTML = '';
                return;
            }

            crumbBar.style.display = 'flex';
            const parts = [];
            if (hasHistory) {
                parts.push('<span>Historial y selección de leyenda conservados</span>');
                parts.push('<span>&rsaquo;</span>');
            }
            if (hasSelection) {
                parts.push(`<span class="crumb-selected-pill">Selección activa: ${selectedJerarquiaLabel}</span>`);
            } else if (hasHistory) {
                parts.push('<span class="crumb-current">Vista anterior restaurada</span>');
            }
            if (hasClusterUrl) {
                parts.push(`<span class="crumb-cluster-id-pill">${activeCtx.red} • cluster_id: ${activeCtx.clusterId}</span>`);
            }
            crumbBar.innerHTML = parts.join(' ');
        }

        function stepBackJerarquiaFilter() {
            if (jerarquiaNavHistory.length > 0) {
                const prevSnapshot = jerarquiaNavHistory.pop();
                restoreFilterSnapshot(prevSnapshot);
                return;
            }
            if (selectedJerarquiaLabel || selectedJerarquiaClusterId) {
                selectedJerarquiaLabel = null;
                selectedJerarquiaClusterId = '';
                selectedJerarquiaRed = '';
                updateJerarquiaNavigationUI(rawPoints);
                updateExecutiveDashboard();
            }
        }

        function renderTopJerarquiasChart(execData) {
            updateJerarquiaNavigationUI(execData);
            const ctx = document.getElementById('chartTopJerarquias').getContext('2d');
            if (chartTopJerarquiasInstance) {
                chartTopJerarquiasInstance.destroy();
                chartTopJerarquiasInstance = null;
            }

            const showRed = jerarquiaDatasetVisibility[0] !== false;
            const showUnit = jerarquiaDatasetVisibility[1] !== false;

            const jerarquiaMap = {};
            execData.forEach(p => {
                if (!p.tiene_falla) return;
                const key = `${p.red}-${p.olt}-${p.cluster}`;
                if (!jerarquiaMap[key]) {
                    jerarquiaMap[key] = {
                        red: 0,
                        unit: 0,
                        redRaw: normalizarClaveRed(p.red),
                        oltRaw: p.olt,
                        clusterRaw: p.cluster,
                        clusterId: String(p.cluster_id || '').trim()
                    };
                } else if (!jerarquiaMap[key].clusterId && String(p.cluster_id || '').trim() !== '') {
                    jerarquiaMap[key].clusterId = String(p.cluster_id).trim();
                }
                if (['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)', 'FAT Afectada'].includes(p.macro_hallazgo)) jerarquiaMap[key].red++;
                else jerarquiaMap[key].unit++;
            });

            const sorted = Object.entries(jerarquiaMap)
                .map(([label, v]) => {
                    const visibleRed = showRed ? v.red : 0;
                    const visibleUnit = showUnit ? v.unit : 0;
                    return {
                        label,
                        total: visibleRed + visibleUnit,
                        allTotal: v.red + v.unit,
                        red: v.red,
                        unit: v.unit,
                        redRaw: v.redRaw,
                        oltRaw: v.oltRaw,
                        clusterRaw: v.clusterRaw,
                        clusterId: v.clusterId
                    };
                })
                .sort((a, b) => b.total - a.total || b.allTotal - a.allTotal);

            document.getElementById('jerarquiasTotalBadge').innerText = `${sorted.length.toLocaleString()} Clústeres`;
            const dynamicHeight = Math.max(370, sorted.length * 28 + 36);
            document.getElementById('jerarquiasCanvasWrapper').style.height = `${dynamicHeight}px`;

            const redBorderColors = sorted.map(s => s.label === selectedJerarquiaLabel ? '#f8fafc' : '#ef4444');
            const redBorderWidths = sorted.map(s => s.label === selectedJerarquiaLabel ? 2.5 : 0);
            const unitBorderColors = sorted.map(s => s.label === selectedJerarquiaLabel ? '#f8fafc' : '#38bdf8');
            const unitBorderWidths = sorted.map(s => s.label === selectedJerarquiaLabel ? 2.5 : 0);

            chartTopJerarquiasInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: sorted.map(s => s.label),
                    datasets: [
                        {
                            label: 'Fallas de Red (OSP)',
                            data: sorted.map(s => s.red),
                            hidden: !showRed,
                            backgroundColor: '#ef4444',
                            borderColor: redBorderColors,
                            borderWidth: redBorderWidths,
                            borderRadius: 3
                        },
                        {
                            label: 'Fallas Unitarias (Cliente)',
                            data: sorted.map(s => s.unit),
                            hidden: !showUnit,
                            backgroundColor: '#38bdf8',
                            borderColor: unitBorderColors,
                            borderWidth: unitBorderWidths,
                            borderRadius: 3
                        }
                    ]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { stacked: true, position: 'top', grid: { color: '#1e293b' }, ticks: { color: '#cbd5e1', font: { size: 10.5 } } },
                        y: { stacked: true, grid: { display: false }, ticks: { color: '#f8fafc', font: { size: 10.5 } } }
                    },
                    plugins: {
                        legend: {
                            labels: { color: '#f8fafc', font: { size: 11 } },
                            onClick: function(e, legendItem, legend) {
                                const idx = legendItem.datasetIndex;
                                const chart = legend.chart;
                                const currentlyVisible = isChartDatasetVisible(chart, idx);
                                const nextVisible = !currentlyVisible;
                                chart.data.datasets[idx].hidden = !nextVisible;
                                chart.getDatasetMeta(idx).hidden = !nextVisible;
                                jerarquiaDatasetVisibility[idx] = nextVisible;
                                chart.update();
                            }
                        },
                        tooltip: {
                            enabled: true,
                            backgroundColor: 'rgba(9, 13, 22, 0.97)',
                            titleColor: '#38bdf8',
                            bodyColor: '#f8fafc',
                            borderColor: '#38bdf8',
                            borderWidth: 1,
                            callbacks: {
                                afterBody: function(items) {
                                    if (!items.length) return '';
                                    const row = sorted[items[0].dataIndex];
                                    const activeRedVisible = jerarquiaDatasetVisibility[0] !== false;
                                    const activeUnitVisible = jerarquiaDatasetVisibility[1] !== false;
                                    const visibleSum = (activeRedVisible ? row.red : 0) + (activeUnitVisible ? row.unit : 0);
                                    const isSelected = selectedJerarquiaLabel === row.label;
                                    const hint = isSelected
                                        ? ' [Clic para volver atrás conservando filtro y leyenda]'
                                        : ' [Clic para filtrar conservando selección previa y leyenda]';
                                    const breakdownParts = [];
                                    if (activeRedVisible) breakdownParts.push(`Red: ${row.red.toLocaleString()}`);
                                    if (activeUnitVisible) breakdownParts.push(`Unitarias: ${row.unit.toLocaleString()}`);
                                    const detailLine = breakdownParts.length > 0
                                        ? ` Total ONTs mostradas: ${visibleSum.toLocaleString()} (${breakdownParts.join(' | ')})`
                                        : ' Ambas series ocultas en leyenda';
                                    const cidLine = row.clusterId ? ` cluster_id (${row.redRaw}): ${row.clusterId}` : ` cluster_id (${row.redRaw}): N/D`;
                                    return [detailLine, cidLine, hint];
                                }
                            }
                        }
                    },
                    onHover: (event, elements) => {
                        const target = event?.native?.target;
                        if (target) target.style.cursor = (elements && elements.length > 0) ? 'pointer' : 'default';
                    },
                    onClick: (e, elements) => {
                        if (!elements.length) return;
                        const item = sorted[elements[0].index];

                        if (selectedJerarquiaLabel === item.label) {
                            stepBackJerarquiaFilter();
                            return;
                        }

                        const prevSnapshot = captureCurrentFilterSnapshot();
                        jerarquiaNavHistory.push(prevSnapshot);
                        selectedJerarquiaLabel = item.label;
                        selectedJerarquiaClusterId = item.clusterId || '';
                        selectedJerarquiaRed = item.redRaw || '';

                        if (item.redRaw && Array.from(document.getElementById('filtroRed').options).some(o => o.value === item.redRaw)) {
                            document.getElementById('filtroRed').value = item.redRaw;
                        }
                        initDropdowns();
                        document.getElementById('filtroOlt').value = item.oltRaw;
                        initDropdowns();
                        document.getElementById('filtroCluster').value = item.clusterRaw;
                        onFilterChanged();
                    }
                }
            });
        }

        function renderOperacionAtencionChart(execData) {
            const ctx = document.getElementById('chartOperacionAtencion').getContext('2d');
            if (chartOperacionInstance) chartOperacionInstance.destroy();

            const redes = [...new Set(execData.map(p => p.red))];
            const sinAccion = redes.map(r => execData.filter(p => p.red === r && p.tipo_atencion === 'Sin Acción').length);
            const preventiva = redes.map(r => execData.filter(p => p.red === r && p.tipo_atencion === 'Preventiva (Proactiva de Red)').length);
            const correctiva = redes.map(r => execData.filter(p => p.red === r && p.tipo_atencion === 'Correctiva (Inmediata)').length);

            chartOperacionInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: redes,
                    datasets: [
                        { label: 'Nominal (Sin Acción)', data: sinAccion, backgroundColor: '#10b981', borderRadius: 4 },
                        { label: 'Preventiva (Proactiva)', data: preventiva, backgroundColor: '#f59e0b', borderRadius: 4 },
                        { label: 'Correctiva (Inmediata)', data: correctiva, backgroundColor: '#ef4444', borderRadius: 4 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { display: false }, ticks: { color: '#f8fafc', font: { weight: 'bold' } } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#cbd5e1' } }
                    },
                    plugins: { legend: { labels: { color: '#f8fafc', font: { size: 11 } } } }
                }
            });
        }

        function renderCableLabsExecChart(execData) {
            const ctx = document.getElementById('chartCableLabsExec').getContext('2d');
            if (chartCableLabsInstance) chartCableLabsInstance.destroy();

            const labels = ['FEC Crítico', 'Desv. OSP (>3dB)', 'Drop / QoE<60', 'Térmico (ΔT≥10)', 'ORL (<32dB)', 'Asim. US'];
            const counts = [
                execData.filter(p => p.is_fec).length,
                execData.filter(p => p.is_osp).length,
                execData.filter(p => p.is_drop).length,
                execData.filter(p => p.is_temp || p.is_clima).length,
                execData.filter(p => p.is_orl).length,
                execData.filter(p => p.is_asimetria_us).length
            ];

            chartCableLabsInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'ONTs con Anomalía',
                        data: counts,
                        backgroundColor: ['#38bdf8', '#10b981', '#f59e0b', '#a855f7', '#06b6d4', '#ec4899'],
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { display: false }, ticks: { color: '#cbd5e1', font: { size: 10 } } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#cbd5e1' } }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        function renderMarcasExecChart(execData) {
            const ctx = document.getElementById('chartMarcasExec').getContext('2d');
            if (chartMarcasInstance) chartMarcasInstance.destroy();

            const brandStats = {};
            execData.forEach(p => {
                const m = p.marca_ont || 'Otras';
                if (!brandStats[m]) brandStats[m] = { ok: 0, fail: 0 };
                if (p.tiene_falla) brandStats[m].fail++;
                else brandStats[m].ok++;
            });

            const brands = Object.keys(brandStats).sort((a, b) => (brandStats[b].ok + brandStats[b].fail) - (brandStats[a].ok + brandStats[a].fail));

            chartMarcasInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: brands,
                    datasets: [
                        { label: 'Nominal', data: brands.map(b => brandStats[b].ok), backgroundColor: '#10b981', borderRadius: 4 },
                        { label: 'Con Falla', data: brands.map(b => brandStats[b].fail), backgroundColor: '#ef4444', borderRadius: 4 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { color: '#f8fafc', font: { size: 11 } } },
                        y: { stacked: true, grid: { color: '#334155' }, ticks: { color: '#cbd5e1' } }
                    },
                    plugins: { legend: { labels: { color: '#f8fafc', font: { size: 11 } } } }
                }
            });
        }

        function renderExecPriorityTable(execData) {
            const tbody = document.getElementById('execPriorityTableBody');
            tbody.innerHTML = '';

            const focos = {};
            execData.forEach(p => {
                if (!['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)', 'FAT Afectada'].includes(p.macro_hallazgo)) return;
                let elem = '';
                let key = '';
                if (p.macro_hallazgo === 'Falla OLT') { elem = p.olt; key = `OLT|${p.red}|${p.olt}`; }
                else if (p.macro_hallazgo === 'Falla Clúster') { elem = p.cluster; key = `CLU|${p.red}|${p.cluster}`; }
                else if (p.macro_hallazgo === 'Puerto (OTDR)') { elem = p.puerto; key = `PUE|${p.red}|${p.puerto}`; }
                else if (p.macro_hallazgo === 'FAT Afectada') { elem = `${p.puerto} -> FAT ${p.fat}`; key = `FAT|${p.red}|${p.olt}|${p.puerto}|${p.fat}`; }

                if (!focos[key]) {
                    focos[key] = { red: p.red, tipo: p.macro_hallazgo, elemento: elem, zona: p.zona, olt: p.olt, cluster: p.cluster, puerto: p.puerto, accion: p.macro_accion_corta, fail: 0 };
                }
                focos[key].fail++;
            });

            const listaFocos = Object.values(focos).map(f => {
                let totalElem = 0;
                if (f.tipo === 'Falla OLT') totalElem = rawPoints.filter(p => p.red === f.red && p.olt === f.olt).length;
                else if (f.tipo === 'Falla Clúster') totalElem = rawPoints.filter(p => p.red === f.red && p.cluster === f.cluster).length;
                else if (f.tipo === 'Puerto (OTDR)') totalElem = rawPoints.filter(p => p.red === f.red && p.puerto === f.puerto).length;
                else totalElem = f.fail;
                const pct = totalElem > 0 ? ((f.fail / totalElem) * 100).toFixed(1) : '100.0';
                return { ...f, totalElem, pct };
            }).sort((a, b) => b.fail - a.fail).slice(0, 25);

            if (listaFocos.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:#94a3b8; padding:20px;">No se detectan fallas masivas de jerarquía (OLT / Clúster / Puerto / FAT) para el filtro seleccionado.</td></tr>';
                return;
            }

            listaFocos.forEach((f, i) => {
                const prioBadge = i < 5
                    ? `<span class="badge-action" style="background:#dc2626; color:white;">P1 - CRÍTICA</span>`
                    : `<span class="badge-action" style="background:#f59e0b; color:#0f172a;">P2 - ALTA</span>`;
                const redBadge = `<span style="background: ${f.red === 'CLARO' ? '#0284c7' : '#f59e0b'}; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold;">${f.red}</span>`;

                tbody.innerHTML += `
                    <tr>
                        <td>${prioBadge}</td>
                        <td>${redBadge}</td>
                        <td><b>${f.tipo}</b></td>
                        <td style="color:#38bdf8;"><b>${f.elemento}</b></td>
                        <td>${f.zona}</td>
                        <td style="color:#ef4444;"><b>${f.fail.toLocaleString()}</b> / ${f.totalElem.toLocaleString()}</td>
                        <td><b>${f.pct}%</b></td>
                        <td style="color:#10b981;">${f.accion}</td>
                        <td><button class="btn-drill" onclick="inspectFromExec('${f.tipo}', '${f.olt}', '${f.cluster}', '${f.puerto}')">Ver Detalle &raquo;</button></td>
                    </tr>
                `;
            });
        }

        function inspectFromExec(tipo, olt, cluster, puerto) {
            if (tipo === 'Falla OLT') document.getElementById('filtroOlt').value = olt;
            else if (tipo === 'Falla Clúster') document.getElementById('filtroCluster').value = cluster;
            else if (tipo === 'Puerto (OTDR)' || tipo === 'FAT Afectada') {
                document.getElementById('filtroOlt').value = olt;
                initDropdowns();
                document.getElementById('filtroPuerto').value = puerto;
            }
            onFilterChanged();
            switchTab('operativo');
        }

        function getColumnSearchValues() {
            const globalVal = (document.getElementById('globalTableSearchV2')?.value || '').trim().toLowerCase();
            const colFilters = Array.from(document.querySelectorAll('#analysisTableV2 .col-search-input')).map(inp => ({
                key: inp.dataset.colKey,
                val: inp.value.trim().toLowerCase()
            }));
            return { globalVal, colFilters };
        }

        function compareTableRows(a, b, key, asc) {
            const va = a[key];
            const vb = b[key];
            const numA = typeof va === 'number' ? va : parseFloat(String(va ?? '').replace(/[^0-9.\-]/g, ''));
            const numB = typeof vb === 'number' ? vb : parseFloat(String(vb ?? '').replace(/[^0-9.\-]/g, ''));
            const bothNum = !Number.isNaN(numA) && !Number.isNaN(numB) && String(va ?? '').trim() !== '' && String(vb ?? '').trim() !== '';

            let cmp = 0;
            if (bothNum) {
                cmp = numA - numB;
            } else {
                cmp = String(va ?? '').localeCompare(String(vb ?? ''), 'es', { numeric: true });
            }
            return asc ? cmp : -cmp;
        }

        function updateSortArrows() {
            document.querySelectorAll('#analysisTableV2 .sort-arrow').forEach(el => {
                el.textContent = '⇅';
                el.style.color = '#94a3b8';
            });
            if (!sortStateV2.key) return;
            const icon = sortStateV2.asc ? '▲' : '▼';
            document.querySelectorAll(`#analysisTableV2 th[data-sort-key="${sortStateV2.key}"] .sort-arrow`).forEach(el => {
                el.textContent = icon;
                el.style.color = '#38bdf8';
            });
        }

        function toggleSortColumn(key) {
            if (sortStateV2.key === key) {
                sortStateV2.asc = !sortStateV2.asc;
            } else {
                sortStateV2.key = key;
                sortStateV2.asc = true;
            }
            refreshTableViewV2(true);
        }

        function onTableFilterInput() {
            refreshTableViewV2(true);
        }

        function clearTableFiltersV2() {
            const gInp = document.getElementById('globalTableSearchV2');
            if (gInp) gInp.value = '';
            document.querySelectorAll('#analysisTableV2 .col-search-input').forEach(inp => {
                inp.value = '';
            });
            sortStateV2 = { key: null, asc: true };
            refreshTableViewV2(true);
        }

        function refreshTableViewV2(resetPage = false) {
            const { globalVal, colFilters } = getColumnSearchValues();
            let filtered = activeFilteredPoints.filter(d => {
                if (globalVal) {
                    const blob = `${d.red} ${d.serial} ${d.zona} ${d.olt} ${d.cluster} ${d.puerto} ${d.fat} ${d.direccion} ${d.macro_hallazgo} ${d.tipo_atencion} ${d.causa_raiz_noc} ${d.macro_accion_corta} ${d.macro_accion_detallada} ${d.sla_priority} ${d.orl_val} ${d.asimetria_rx_db} ${d.afectacion_asimetria} ${d.marca_ont} ${d.condicion} ${d.impacto} ${d.distancia} ${d.delta_loss} ${d._osp_base} ${d.fec_status} ${d.delta_temp} ${d.x} ${d.y} ${d.tx} ${d.bias} ${d.temp} ${d.voltage} ${d.qoe}`.toLowerCase();
                    if (!blob.includes(globalVal)) return false;
                }
                for (const cf of colFilters) {
                    if (!cf.val) continue;
                    const rawVal = String(d[cf.key] ?? '').toLowerCase();
                    if (!rawVal.includes(cf.val)) return false;
                }
                return true;
            });

            if (sortStateV2.key) {
                filtered = [...filtered].sort((a, b) => compareTableRows(a, b, sortStateV2.key, sortStateV2.asc));
            }

            tableViewPoints = filtered;
            updateSortArrows();
            if (resetPage) currentPageV2 = 1;
            updateTablePageV2();
        }

        function updateTablePageV2() {
            const totalRows = tableViewPoints.length;
            const totalPages = Math.ceil(totalRows / rowsPerPage) || 1;
            if (currentPageV2 > totalPages) currentPageV2 = totalPages;
            if (currentPageV2 < 1) currentPageV2 = 1;

            const startIdx = (currentPageV2 - 1) * rowsPerPage;
            const pageData = tableViewPoints.slice(startIdx, startIdx + rowsPerPage);
            const tbody = document.getElementById('tableBodyV2');
            tbody.innerHTML = '';

            pageData.forEach(d => {
                const redBadge = `<span style="background: ${d.red === 'CLARO' ? '#0284c7' : '#f59e0b'}; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold;">${d.red}</span>`;
                const tipoAtencionBadge = d.tipo_atencion === 'Correctiva (Inmediata)'
                    ? `<span style="color: #ef4444; font-weight: bold;">${d.tipo_atencion}</span>`
                    : (d.tipo_atencion === 'Preventiva (Proactiva de Red)' ? `<span style="color: #f59e0b; font-weight: bold;">${d.tipo_atencion}</span>` : `<span style="color: #94a3b8;">${d.tipo_atencion}</span>`);

                const asimColor = d.is_asimetria_us
                    ? '#ef4444'
                    : (String(d.afectacion_asimetria).startsWith('MODERADA') ? '#f59e0b' : '#10b981');

                tbody.innerHTML += `
                    <tr>
                        <td>${redBadge}</td>
                        <td><b>${d.serial}</b></td>
                        <td>${d.zona}</td>
                        <td>${d.olt}</td>
                        <td>${d.cluster}</td>
                        <td>${d.puerto}</td>
                        <td><b>${d.fat}</b></td>
                        <td>${d.direccion}</td>
                        <td><b>${d.macro_hallazgo}</b></td>
                        <td>${tipoAtencionBadge}</td>
                        <td>${d.causa_raiz_noc}</td>
                        <td style="color: #10b981;">${d.macro_accion_corta}</td>
                        <td style="color: #cbd5e1; font-size: 11px;">${d.macro_accion_detallada}</td>
                        <td style="color: #ef4444; font-weight: bold;">${d.sla_priority}</td>
                        <td style="color: #06b6d4;">${d.orl_val} dB</td>
                        <td style="color: ${asimColor}; font-weight: bold;">${Number(d.asimetria_rx_db) >= 0 ? '+' : ''}${Number(d.asimetria_rx_db).toFixed(2)} dB</td>
                        <td style="color: ${asimColor}; font-size: 11px;"><b>${d.afectacion_asimetria}</b></td>
                        <td><b>${d.marca_ont}</b></td>
                        <td><b>${d.condicion}</b></td>
                        <td>${d.impacto}</td>
                        <td>${d.distancia}</td>
                        <td style="color:#f59e0b"><b>${d.delta_loss} dB</b></td>
                        <td>18.5 dB</td>
                        <td style="color:#ef4444"><b>+${d.delta_loss} dB</b></td>
                        <td>${d.fec_status}</td>
                        <td>${d.delta_temp} °C</td>
                        <td style="color: #38bdf8;">${d.x} dBm</td>
                        <td style="color: #38bdf8;">${d.y} dBm</td>
                        <td>${d.tx} dBm</td>
                        <td style="color: #a855f7;">${d.bias} mA</td>
                        <td>${d.temp} °C</td>
                        <td>${d.voltage} mV</td>
                        <td><b>${d.qoe}</b></td>
                    </tr>
                `;
            });
            document.getElementById('tableCountInfoV2').innerText = `Mostrando ${totalRows > 0 ? startIdx + 1 : 0}-${Math.min(startIdx + rowsPerPage, totalRows)} de ${totalRows.toLocaleString()}`;
            document.getElementById('pageIndicatorV2').innerText = `Página ${currentPageV2} de ${totalPages}`;
            document.getElementById('btnPrevV2').disabled = (currentPageV2 <= 1);
            document.getElementById('btnNextV2').disabled = (currentPageV2 >= totalPages);
        }

        function changePageV2(d) { currentPageV2 += d; updateTablePageV2(); }

        function onFilterChanged() {
            closeSummaryPanel();
            applyFilters();
            initDropdowns();
            updateExecutiveDashboard();
        }

        function applyFilters() {
            const red = document.getElementById('filtroRed').value;
            const zona = document.getElementById('filtroLocalidad').value;
            const olt = document.getElementById('filtroOlt').value;
            const cluster = document.getElementById('filtroCluster').value;
            const puerto = document.getElementById('filtroPuerto').value;
            const condicion = document.getElementById('filtroCondicion').value;
            const impacto = document.getElementById('filtroImpacto').value;

            activeFilteredPoints = rawPoints.filter(p => {
                if (red !== 'TODAS' && p.red !== red) return false;
                if (zona !== 'TODAS' && p.zona !== zona) return false;
                if (olt !== 'TODAS' && p.olt !== olt) return false;
                if (cluster !== 'TODOS' && p.cluster !== cluster) return false;
                if (puerto !== 'TODOS' && p.puerto !== puerto) return false;
                if (condicion !== 'TODAS' && p.condicion !== condicion) return false;
                if (impacto !== 'TODOS' && p.impacto !== impacto) return false;
                if (filterOnlyFailures && !p.tiene_falla) return false;
                if (currentSymptomFilter !== 'TODOS' && p.categoria !== currentSymptomFilter) return false;
                if (currentAttentionFilter !== 'TODOS' && p.tipo_atencion !== currentAttentionFilter) return false;
                if (currentMacroFilter !== 'TODOS' && p.macro_hallazgo !== currentMacroFilter) return false;
                if (currentBrandFilter !== 'TODOS' && p.marca_ont !== currentBrandFilter) return false;
                if (currentAdvancedFilter === 'fec' && !p.is_fec) return false;
                if (currentAdvancedFilter === 'osp' && !p.is_osp) return false;
                if (currentAdvancedFilter === 'drop' && !p.is_drop) return false;
                if (currentAdvancedFilter === 'temp' && !(p.is_temp || p.is_clima)) return false;
                if (currentAdvancedFilter === 'orl' && !p.is_orl) return false;
                if (currentAdvancedFilter === 'sla' && !['Falla OLT', 'Falla Clúster', 'Puerto (OTDR)'].includes(p.macro_hallazgo)) return false;
                if (currentAdvancedFilter === 'asim' && !p.is_asimetria_us) return false;
                return true;
            });

            currentPageV2 = 1;
            updateKpiCards();
            refreshTableViewV2(true);
        }

        function resetFilters() {
            document.getElementById('filtroRed').value = 'TODAS';
            document.getElementById('filtroLocalidad').value = 'TODAS';
            document.getElementById('filtroOlt').value = 'TODAS';
            document.getElementById('filtroCluster').value = 'TODOS';
            document.getElementById('filtroPuerto').value = 'TODOS';
            document.getElementById('filtroCondicion').value = 'TODAS';
            document.getElementById('filtroImpacto').value = 'TODOS';

            jerarquiaNavHistory = [];
            selectedJerarquiaLabel = null;
            selectedJerarquiaClusterId = '';
            selectedJerarquiaRed = '';
            jerarquiaDatasetVisibility = [true, true];
            currentSymptomFilter = 'TODOS';
            currentAttentionFilter = 'TODOS';
            currentMacroFilter = 'TODOS';
            currentBrandFilter = 'TODOS';
            currentAdvancedFilter = 'TODOS';
            filterOnlyFailures = false;
            activeFilteredPoints = rawPoints;

            document.querySelectorAll('.kpi-card').forEach(c => c.classList.remove('active-filter'));
            clearTableFiltersV2();
            closeSummaryPanel();
            initDropdowns();
            updateKpiCards();
            refreshTableViewV2(true);
            updateExecutiveDashboard();
        }

        function drillDownFilter(tipo, val1, val2) {
            if (tipo === 'OLT') document.getElementById('filtroOlt').value = val1;
            else if (tipo === 'Puerto PON') {
                document.getElementById('filtroOlt').value = val1;
                initDropdowns();
                document.getElementById('filtroPuerto').value = val2;
            } else if (tipo === 'Cluster') document.getElementById('filtroCluster').value = val1;
            else if (tipo === 'FAT') {
                if (val1) document.getElementById('filtroOlt').value = val1;
                initDropdowns();
                if (val2) document.getElementById('filtroPuerto').value = val2;
            }
            applyFilters();
            initDropdowns();
            updateExecutiveDashboard();
        }

        function renderSummaryTable(macroType) {
            const wrapper = document.getElementById('summaryWrapper');
            const panel1 = document.getElementById('summaryPanel');
            const panel2 = document.getElementById('summaryPanelSecundario');
            const title1 = document.getElementById('summaryTitle');
            const thead1 = document.getElementById('summaryThead');
            const tbody1 = document.getElementById('summaryTbody');
            const title2 = document.getElementById('summaryTitleSec');
            const thead2 = document.getElementById('summaryTheadSec');
            const tbody2 = document.getElementById('summaryTbodySec');

            wrapper.style.display = 'flex';
            panel1.style.display = 'block';
            panel2.style.display = 'block';
            tbody1.innerHTML = '';
            tbody2.innerHTML = '';

            currentMacroFilter = macroType;
            applyFilters();
            const dataToGroup = activeFilteredPoints;

            if (macroType === 'Falla OLT') {
                title1.innerText = 'RESUMEN OLTS AFECTADAS (>90% AFECTACIÓN) [Haz clic para filtrar]';
                thead1.innerHTML = '<tr><th>Red</th><th>OLT</th><th>ONTs Afectadas</th><th>Total OLT</th><th>% Afectación</th></tr>';
                const grouped1 = {};
                dataToGroup.forEach(p => {
                    const key = `${p.red}_${p.olt}`;
                    if (!grouped1[key]) grouped1[key] = { red: p.red, olt: p.olt, fail: 0 };
                    grouped1[key].fail++;
                });
                let rows1 = Object.values(grouped1).map(g => {
                    const totalOlt = rawPoints.filter(p => p.red === g.red && p.olt === g.olt).length;
                    const pct = totalOlt > 0 ? (g.fail / totalOlt) * 100 : 0;
                    return { ...g, totalOlt, pct };
                }).sort((a, b) => b.fail - a.fail || b.pct - a.pct);

                let sumFail1 = 0, sumTotal1 = 0;
                rows1.forEach(g => {
                    sumFail1 += g.fail; sumTotal1 += g.totalOlt;
                    tbody1.innerHTML += `<tr class="clickable-row" onclick="drillDownFilter('OLT', '${g.olt}')"><td><b>${g.red}</b></td><td><b style="color:#38bdf8">${g.olt}</b></td><td style="color:#ef4444"><b>${g.fail.toLocaleString()}</b></td><td>${g.totalOlt.toLocaleString()}</td><td style="color:#ef4444"><b>${g.pct.toFixed(1)}%</b></td></tr>`;
                });
                tbody1.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td style="color:#ef4444">${sumFail1.toLocaleString()}</td><td>${sumTotal1.toLocaleString()}</td><td>${sumTotal1 > 0 ? ((sumFail1/sumTotal1)*100).toFixed(1) : 0}%</td></tr>`;

                title2.innerText = 'CANTIDAD DE OLT POR RED CON FALLA';
                thead2.innerHTML = '<tr><th>Red</th><th>Cantidad OLT Falla</th></tr>';
                const grouped2 = {};
                dataToGroup.forEach(p => {
                    if (!grouped2[p.red]) grouped2[p.red] = new Set();
                    grouped2[p.red].add(p.olt);
                });
                let sumOltsCnt = 0;
                Object.keys(grouped2).map(red => ({ red, cnt: grouped2[red].size })).sort((a, b) => b.cnt - a.cnt).forEach(g => {
                    sumOltsCnt += g.cnt;
                    tbody2.innerHTML += `<tr><td><b>${g.red}</b></td><td style="color:#38bdf8"><b>${g.cnt.toLocaleString()}</b></td></tr>`;
                });
                tbody2.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td style="color:#38bdf8">${sumOltsCnt.toLocaleString()}</td></tr>`;

            } else if (macroType === 'Falla Clúster') {
                title1.innerText = 'RESUMEN CLÚSTERES AFECTADOS (>90% AFECTACIÓN) [Haz clic para filtrar]';
                thead1.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Clúster</th><th>ONTs Afectadas</th><th>Total Clúster</th><th>% Afectación</th></tr>';
                const grouped1 = {};
                dataToGroup.forEach(p => {
                    const key = `${p.red}_${p.cluster}`;
                    if (!grouped1[key]) grouped1[key] = { red: p.red, olt: p.olt, cluster: p.cluster, fail: 0 };
                    grouped1[key].fail++;
                });
                let rows1 = Object.values(grouped1).map(g => {
                    const totalClu = rawPoints.filter(p => p.red === g.red && p.cluster === g.cluster).length;
                    const pct = totalClu > 0 ? (g.fail / totalClu) * 100 : 0;
                    return { ...g, totalClu, pct };
                }).sort((a, b) => b.fail - a.fail || b.pct - a.pct);

                let sumFailClu = 0, sumTotalClu = 0;
                rows1.forEach(g => {
                    sumFailClu += g.fail; sumTotalClu += g.totalClu;
                    tbody1.innerHTML += `<tr class="clickable-row" onclick="drillDownFilter('Cluster', '${g.cluster}')"><td><b>${g.red}</b></td><td>${g.olt}</td><td><b style="color:#38bdf8">${g.cluster}</b></td><td style="color:#f59e0b"><b>${g.fail.toLocaleString()}</b></td><td>${g.totalClu.toLocaleString()}</td><td style="color:#f59e0b"><b>${g.pct.toFixed(1)}%</b></td></tr>`;
                });
                tbody1.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td>-</td><td style="color:#f59e0b">${sumFailClu.toLocaleString()}</td><td>${sumTotalClu.toLocaleString()}</td><td>${sumTotalClu > 0 ? ((sumFailClu/sumTotalClu)*100).toFixed(1) : 0}%</td></tr>`;

                title2.innerText = 'CANTIDAD DE CLÚSTER POR OLT CON FALLA';
                thead2.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Cant. Clúster Falla</th></tr>';
                const grouped2 = {};
                dataToGroup.forEach(p => {
                    const oltKey = `${p.red}_${p.olt}`;
                    if (!grouped2[oltKey]) grouped2[oltKey] = { red: p.red, olt: p.olt, clusters: new Set() };
                    grouped2[oltKey].clusters.add(p.cluster);
                });
                let sumCluCnt = 0;
                Object.values(grouped2).map(g => ({ red: g.red, olt: g.olt, cnt: g.clusters.size })).sort((a, b) => b.cnt - a.cnt).forEach(g => {
                    sumCluCnt += g.cnt;
                    tbody2.innerHTML += `<tr><td><b>${g.red}</b></td><td>${g.olt}</td><td style="color:#38bdf8"><b>${g.cnt.toLocaleString()}</b></td></tr>`;
                });
                tbody2.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td style="color:#38bdf8">${sumCluCnt.toLocaleString()}</td></tr>`;

            } else if (macroType === 'Puerto (OTDR)') {
                title1.innerText = 'RESUMEN PUERTOS PON CON FALLA (100% AFECTACIÓN) [Haz clic para filtrar]';
                thead1.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Puerto PON</th><th>ONTs Afectadas / Total</th><th>% Afectación</th></tr>';
                const grouped1 = {};
                dataToGroup.forEach(p => {
                    const key = `${p.red}_${p.olt}_${p.puerto}`;
                    if (!grouped1[key]) grouped1[key] = { red: p.red, olt: p.olt, puerto: p.puerto, fail: 0 };
                    grouped1[key].fail++;
                });
                let rows1 = Object.values(grouped1).map(g => {
                    const totalPue = rawPoints.filter(p => p.red === g.red && p.olt === g.olt && p.puerto === g.puerto).length;
                    const pct = totalPue > 0 ? (g.fail / totalPue) * 100 : 0;
                    return { ...g, totalPue, pct };
                }).sort((a, b) => b.fail - a.fail);

                let sumFailPue = 0, sumTotalPue = 0;
                rows1.forEach(g => {
                    sumFailPue += g.fail; sumTotalPue += g.totalPue;
                    tbody1.innerHTML += `<tr class="clickable-row" onclick="drillDownFilter('Puerto PON', '${g.olt}', '${g.puerto}')"><td><b>${g.red}</b></td><td>${g.olt}</td><td><b style="color:#38bdf8">${g.puerto}</b></td><td style="color:#ef4444"><b>${g.fail} / ${g.totalPue}</b></td><td style="color:#ef4444"><b>${g.pct.toFixed(1)}%</b></td></tr>`;
                });
                tbody1.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td>-</td><td style="color:#ef4444">${sumFailPue.toLocaleString()} / ${sumTotalPue.toLocaleString()}</td><td>${sumTotalPue > 0 ? ((sumFailPue/sumTotalPue)*100).toFixed(1) : 0}%</td></tr>`;

                title2.innerText = 'CANTIDAD DE PUERTOS PON CON FALLA';
                thead2.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Cluster</th><th>Cant. Puertos Falla</th></tr>';
                const grouped2 = {};
                dataToGroup.forEach(p => {
                    const cluKey = `${p.red}_${p.olt}_${p.cluster}`;
                    if (!grouped2[cluKey]) grouped2[cluKey] = { red: p.red, olt: p.olt, cluster: p.cluster, puertos: new Set() };
                    grouped2[cluKey].puertos.add(p.puerto);
                });
                let sumPueCnt = 0;
                Object.values(grouped2).map(g => ({ red: g.red, olt: g.olt, cluster: g.cluster, cnt: g.puertos.size })).sort((a, b) => b.cnt - a.cnt).forEach(g => {
                    sumPueCnt += g.cnt;
                    tbody2.innerHTML += `<tr><td><b>${g.red}</b></td><td>${g.olt}</td><td>${g.cluster}</td><td style="color:#38bdf8"><b>${g.cnt.toLocaleString()}</b></td></tr>`;
                });
                tbody2.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td>-</td><td style="color:#38bdf8">${sumPueCnt.toLocaleString()}</td></tr>`;

            } else if (macroType === 'FAT Afectada') {
                title1.innerText = 'RESUMEN CAJAS FAT AFECTADAS (100% AFECTACIÓN) [Haz clic para filtrar]';
                thead1.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Puerto PON</th><th>Caja FAT</th><th>ONTs Afectadas / Total</th><th>% Afectación</th></tr>';
                const grouped1 = {};
                dataToGroup.forEach(p => {
                    if (p.fat !== '') {
                        const key = `${p.red}_${p.olt}_${p.puerto}_${p.fat}`;
                        if (!grouped1[key]) grouped1[key] = { red: p.red, olt: p.olt, puerto: p.puerto, fat: p.fat, fail: 0 };
                        grouped1[key].fail++;
                    }
                });
                let rows1 = Object.values(grouped1).map(g => {
                    const totalFat = rawPoints.filter(p => p.red === g.red && p.olt === g.olt && p.puerto === g.puerto && p.fat === g.fat).length;
                    const pct = totalFat > 0 ? (g.fail / totalFat) * 100 : 0;
                    return { ...g, totalFat, pct };
                }).sort((a, b) => b.fail - a.fail);

                let sumFailFat = 0, sumTotalFat = 0;
                rows1.forEach(g => {
                    sumFailFat += g.fail; sumTotalFat += g.totalFat;
                    tbody1.innerHTML += `<tr class="clickable-row" onclick="drillDownFilter('FAT', '${g.olt}', '${g.puerto}')"><td><b>${g.red}</b></td><td>${g.olt}</td><td>${g.puerto}</td><td><b style="color:#38bdf8">${g.fat}</b></td><td style="color:#f59e0b"><b>${g.fail} / ${g.totalFat}</b></td><td style="color:#f59e0b"><b>${g.pct.toFixed(1)}%</b></td></tr>`;
                });
                tbody1.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td>-</td><td>-</td><td style="color:#f59e0b">${sumFailFat.toLocaleString()} / ${sumTotalFat.toLocaleString()}</td><td>${sumTotalFat > 0 ? ((sumFailFat/sumTotalFat)*100).toFixed(1) : 0}%</td></tr>`;

                title2.innerText = 'CANTIDAD DE CAJAS FAT CON FALLA';
                thead2.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Cluster</th><th>Cant. FAT Falla</th></tr>';
                const grouped2 = {};
                dataToGroup.forEach(p => {
                    if (p.fat !== '') {
                        const cluKey = `${p.red}_${p.olt}_${p.cluster}`;
                        if (!grouped2[cluKey]) grouped2[cluKey] = { red: p.red, olt: p.olt, cluster: p.cluster, fats: new Set() };
                        grouped2[cluKey].fats.add(p.fat);
                    }
                });
                let sumFatCnt = 0;
                Object.values(grouped2).map(g => ({ red: g.red, olt: g.olt, cluster: g.cluster, cnt: g.fats.size })).sort((a, b) => b.cnt - a.cnt).forEach(g => {
                    sumFatCnt += g.cnt;
                    tbody2.innerHTML += `<tr><td><b>${g.red}</b></td><td>${g.olt}</td><td>${g.cluster}</td><td style="color:#38bdf8"><b>${g.cnt.toLocaleString()}</b></td></tr>`;
                });
                tbody2.innerHTML += `<tr class="summary-table-total-row"><td>TOTAL</td><td>-</td><td>-</td><td style="color:#38bdf8">${sumFatCnt.toLocaleString()}</td></tr>`;

            } else if (macroType === 'Falla Cliente') {
                title1.innerText = 'RESUMEN DE CLIENTES AFECTADOS / FAT NO IDENTIFICADA';
                thead1.innerHTML = '<tr><th>Red</th><th>OLT</th><th>Puerto PON</th><th>Caja FAT</th><th>Serie ONT</th><th>Causa Raíz</th></tr>';
                dataToGroup.slice(0, 100).forEach(p => {
                    tbody1.innerHTML += `<tr><td><b>${p.red}</b></td><td>${p.olt}</td><td>${p.puerto}</td><td>${p.fat || '(Sin FAT)'}</td><td><b>${p.serial}</b></td><td style="color:#38bdf8">${p.causa_raiz_noc}</td></tr>`;
                });
                panel2.style.display = 'none';
            } else {
                closeSummaryPanel();
            }
        }

        function closeSummaryPanel() {
            document.getElementById('summaryWrapper').style.display = 'none';
            currentMacroFilter = 'TODOS';
            applyFilters();
        }

        function filterBySymptom(s) {
            filterOnlyFailures = false;
            currentAttentionFilter = 'TODOS';
            currentSymptomFilter = (currentSymptomFilter === s ? 'TODOS' : s);
            applyFilters();
        }

        function filterByAttentionType(type) {
            filterOnlyFailures = false;
            currentSymptomFilter = 'TODOS';
            currentAttentionFilter = (currentAttentionFilter === type ? 'TODOS' : type);
            applyFilters();
        }

        function filterByFallas() {
            filterOnlyFailures = !filterOnlyFailures;
            currentSymptomFilter = 'TODOS';
            currentAttentionFilter = 'TODOS';
            applyFilters();
        }

        function filterByMacroCard(m) {
            filterOnlyFailures = false;
            if (currentMacroFilter === m) closeSummaryPanel();
            else renderSummaryTable(m);
        }

        function filterByBrand(b) {
            currentBrandFilter = (currentBrandFilter === b ? 'TODOS' : b);
            applyFilters();
        }

        function filterByAdvancedParam(param) {
            currentAdvancedFilter = (currentAdvancedFilter === param ? 'TODOS' : param);
            applyFilters();
        }

        function exportToExcel(type) {
            const dataToExport = type === 'cablelabs' ? tableViewPoints : activeFilteredPoints;
            const worksheet = XLSX.utils.json_to_sheet(dataToExport);
            const workbook = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(workbook, worksheet, "Claro_VTR_Report");
            XLSX.writeFile(workbook, `Matriz_Claro_VTR_${type}_${new Date().toISOString().slice(0,10)}.xlsx`);
        }

        try {
            initDropdowns();
            applyFilters();
            updateExecutiveDashboard();
        } catch (err) {
            showRuntimeError((err && err.message) || String(err));
        }
    </script>
</body>
</html>
"""

  json_payload = json.dumps(puntos_matriz, ensure_ascii=False).replace(
      "</", "<\\/"
  )
  json_url_map = json.dumps(URL_BASE_CLUSTERVIEW_POR_RED, ensure_ascii=False)
  final_html = (
      html_template.replace("__JSON_URL_CLUSTERVIEW_MAP__", json_url_map)
      .replace("__FECHA_ACTUALIZACION__", fecha_actualizacion)
      .replace("__JSON_MATRIZ_PUNTOS__", json_payload)
  )

  output_html = "Dashboard_Claro_VTR_Final.html"
  with open(output_html, "w", encoding="utf-8") as f:
    f.write(final_html)

  print(
      f"[ÉXITO] Dashboard generado en '{output_html}' ({len(puntos_matriz)}"
      " registros) con funcionalidad alineada a Integratel."
  )


if __name__ == "__main__":
  generar_dashboard()