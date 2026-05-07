from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests
import json
import os
import threading
import time
from datetime import date

app = Flask(__name__)
CORS(app)

# ─── CONFIGURAÇÕES Z-API ─────────────────────────────────────
INSTANCE_ID  = "3F2967D739C8B1FDA5F712DF64D7129F"
TOKEN        = "95274EF3A39CD1EF2493B0D3"
CLIENT_TOKEN = "F3b9c293a698a4934880002da72309a6bS"
API_URL      = f"https://api.z-api.io/instances/{INSTANCE_ID}/token/{TOKEN}/send-image"

COLUNA_TELEFONE = "Telefone do cliente"
COLUNA_NOME     = "Nome do cliente"
COLUNA_IMOVEL   = "Título do imóvel"

ARQUIVO_PROGRESSO = "progresso.json"

# ─── ESTADO GLOBAL DO DISPARO ────────────────────────────────
estado = {
    "rodando": False,
    "total": 0,
    "enviados": 0,
    "erros": 0,
    "log": []
}

def carregar_progresso():
    if os.path.exists(ARQUIVO_PROGRESSO):
        with open(ARQUIVO_PROGRESSO, 'r') as f:
            return json.load(f)
    return {"enviados": []}

def salvar_progresso(progresso):
    with open(ARQUIVO_PROGRESSO, 'w') as f:
        json.dump(progresso, f)

def disparar(contatos, mensagem, url_imagem, intervalo, limite, campanha):
    global estado

    progresso = carregar_progresso()
    ja_enviados = set(progresso["enviados"])

    headers = {
        "Content-Type": "application/json",
        "client-token": CLIENT_TOKEN
    }

    lote = contatos[:limite]
    estado["total"] = len(lote)
    estado["enviados"] = 0
    estado["erros"] = 0
    estado["log"] = []
    estado["rodando"] = True

    for c in lote:
        if not estado["rodando"]:
            break

        telefone = c.get("telefone", "")
        nome     = c.get("nome", "Cliente")
        imovel   = c.get("imovel", "imóvel")

        msg_formatada = mensagem.format(nome=nome, imovel=imovel)

        payload = {
            "phone": telefone,
            "image": url_imagem,
            "caption": msg_formatada
        }

        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=20)
            if resp.status_code == 200:
                estado["enviados"] += 1
                estado["log"].append({"nome": nome, "telefone": telefone, "status": "enviado"})
                ja_enviados.add(telefone)
            else:
                estado["erros"] += 1
                estado["log"].append({"nome": nome, "telefone": telefone, "status": f"erro_{resp.status_code}"})
        except Exception as e:
            estado["erros"] += 1
            estado["log"].append({"nome": nome, "telefone": telefone, "status": "excecao"})

        time.sleep(intervalo)

    progresso["enviados"] = list(ja_enviados)
    salvar_progresso(progresso)

    # Salvar relatório
    relatorio_path = f"relatorio_{campanha}_{date.today()}.xlsx"
    pd.DataFrame(estado["log"]).to_excel(relatorio_path, index=False)

    estado["rodando"] = False

# ─── ROTAS ───────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ZapImovel backend rodando ✅"})

@app.route("/upload", methods=["POST"])
def upload():
    if "arquivo" not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400

    arquivo = request.files["arquivo"]
    nome = arquivo.filename.lower()

    try:
        if nome.endswith(".csv"):
            df = pd.read_csv(arquivo)
        else:
            df = pd.read_excel(arquivo, engine="openpyxl")

        df[COLUNA_TELEFONE] = df[COLUNA_TELEFONE].astype(str).str.replace(r'\D', '', regex=True)
        df = df[df[COLUNA_TELEFONE].str.len() >= 10]

        progresso = carregar_progresso()
        ja_enviados = set(progresso["enviados"])
        df = df[~df[COLUNA_TELEFONE].isin(ja_enviados)]

        contatos = []
        for _, row in df.iterrows():
            contatos.append({
                "nome": str(row.get(COLUNA_NOME, "Cliente")).strip().title(),
                "telefone": str(row[COLUNA_TELEFONE]),
                "imovel": str(row.get(COLUNA_IMOVEL, "imóvel"))
            })

        return jsonify({
            "total": len(contatos),
            "preview": contatos[:5],
            "contatos": contatos
        })

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/disparar", methods=["POST"])
def iniciar_disparo():
    global estado

    if estado["rodando"]:
        return jsonify({"erro": "Já existe um disparo em andamento"}), 400

    data = request.json
    contatos   = data.get("contatos", [])
    mensagem   = data.get("mensagem", "Oi, {nome}!")
    url_imagem = data.get("url_imagem", "https://i.ibb.co/XxYqKkjn/imagem.jpg")
    intervalo  = int(data.get("intervalo", 5))
    limite     = int(data.get("limite", 300))
    campanha   = data.get("campanha", "campanha")

    if not contatos:
        return jsonify({"erro": "Nenhum contato para disparar"}), 400

    t = threading.Thread(target=disparar, args=(contatos, mensagem, url_imagem, intervalo, limite, campanha))
    t.daemon = True
    t.start()

    return jsonify({"status": "Disparo iniciado", "total": min(len(contatos), limite)})

@app.route("/status", methods=["GET"])
def status():
    return jsonify(estado)

@app.route("/parar", methods=["POST"])
def parar():
    estado["rodando"] = False
    return jsonify({"status": "Disparo interrompido"})

@app.route("/resetar", methods=["POST"])
def resetar():
    if os.path.exists(ARQUIVO_PROGRESSO):
        os.remove(ARQUIVO_PROGRESSO)
    return jsonify({"status": "Progresso resetado"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
