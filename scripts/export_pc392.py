import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

# =====================================================================
# CONFIGURAÇÃO
# =====================================================================

USERNAME = os.environ.get("ARCGIS_USERNAME")
PASSWORD = os.environ.get("ARCGIS_PASSWORD")

if not USERNAME or not PASSWORD:
    sys.exit(
        "Erro: defina as variáveis de ambiente ARCGIS_USERNAME e "
        "ARCGIS_PASSWORD antes de rodar este script."
    )

ITEM_ID = "e21f3c1c57754af2910883ba12b508a7"
LAYER_INDEX = 0

# Override manual, caso a resolução automática da URL do serviço falhe
# (ex.: item de outro tipo que não expõe "url" direto no item info).
SERVICE_URL_OVERRIDE = os.environ.get("ARCGIS_SERVICE_URL")

FIELDS = ["objectid", "data_relatorio", "lider_tecnico", "equipe_1", "equipe_2", "ligacao_total"]
DOMAIN_FIELDS = ["lider_tecnico", "equipe_1", "equipe_2"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "..", "data", "pc392.json")


# =====================================================================
# AUTENTICAÇÃO
# =====================================================================

def get_token():
    resp = requests.post(
        "https://www.arcgis.com/sharing/rest/generateToken",
        data={
            "username": USERNAME,
            "password": PASSWORD,
            "referer": "https://www.arcgis.com",
            "f": "json",
            "expiration": 1440,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        raise Exception(f"Falha ao gerar token ArcGIS: {data}")
    return data["token"]


# =====================================================================
# RESOLUÇÃO DA URL DO SERVIÇO A PARTIR DO ITEM ID
# =====================================================================

def get_service_url(token):
    if SERVICE_URL_OVERRIDE:
        return SERVICE_URL_OVERRIDE

    resp = requests.get(
        f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}",
        params={"f": "json", "token": token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise Exception(f"Erro ao resolver item {ITEM_ID}: {data['error']}")
    url = data.get("url")
    if not url:
        raise Exception(
            f"Item {ITEM_ID} não retornou uma URL de serviço. "
            "Defina ARCGIS_SERVICE_URL manualmente com a URL do FeatureServer."
        )
    return url.rstrip("/")


# =====================================================================
# DOMÍNIOS (equivalente a DomainName() do Arcade)
# =====================================================================

def get_domain_maps(token, service_url, layer_index, fields):
    resp = requests.get(
        f"{service_url}/{layer_index}",
        params={"f": "json", "token": token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise Exception(f"Erro ao ler schema da camada {layer_index}: {data['error']}")

    domain_maps = {}
    for field_def in data.get("fields", []):
        name = field_def.get("name")
        if name not in fields:
            continue
        domain = field_def.get("domain")
        if domain and domain.get("type") == "codedValue":
            domain_maps[name] = {
                str(cv["code"]): cv["name"] for cv in domain.get("codedValues", [])
            }
    return domain_maps


def resolve_name(domain_map, code):
    if code is None or (isinstance(code, float) and pd.isna(code)):
        return None
    resolved = domain_map.get(str(code), str(code)) if domain_map else str(code)
    resolved = resolved.strip() if resolved else None
    return resolved or None


# =====================================================================
# BUSCA DE DADOS (com paginação)
# =====================================================================

def get_layer_data(token, service_url, layer_index, out_fields, page_size=1000):
    url = f"{service_url}/{layer_index}/query"
    all_features = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": ",".join(out_fields),
            "f": "json",
            "token": token,
            "resultRecordCount": page_size,
            "resultOffset": offset,
        }
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise Exception(f"Erro do ArcGIS na camada {layer_index}: {data['error']}")
        feats = data.get("features", [])
        if not feats:
            break
        all_features.extend(feats)
        if len(feats) < page_size:
            break
        offset += page_size

    records = [f["attributes"] for f in all_features]
    df = pd.DataFrame.from_records(records)
    print(f"Camada {layer_index}: {len(df)} registros")
    return df


# =====================================================================
# MONTAGEM DOS REGISTROS EXPORTADOS
# =====================================================================

def build_registros(df, domain_maps):
    registros = []
    for _, row in df.iterrows():
        participantes = []
        for field in DOMAIN_FIELDS:
            nome = resolve_name(domain_maps.get(field), row.get(field))
            if nome and nome not in participantes:
                participantes.append(nome)

        data_relatorio = row.get("data_relatorio")
        if pd.isna(data_relatorio):
            continue
        data_str = pd.to_datetime(data_relatorio, unit="ms", utc=True).strftime("%Y-%m-%d")

        ligacoes = row.get("ligacao_total")
        ligacoes = float(ligacoes) if pd.notna(ligacoes) else 0.0

        registros.append({
            "id": int(row["objectid"]),
            "data": data_str,
            "ligacoes": ligacoes,
            "participantes": participantes,
        })
    return registros


def main():
    token = get_token()
    service_url = get_service_url(token)
    domain_maps = get_domain_maps(token, service_url, LAYER_INDEX, DOMAIN_FIELDS)
    df = get_layer_data(token, service_url, LAYER_INDEX, FIELDS)
    registros = build_registros(df, domain_maps)

    output = {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registros": registros,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(registros)} registros salvos em {os.path.abspath(OUTPUT_PATH)}")


if __name__ == "__main__":
    main()
