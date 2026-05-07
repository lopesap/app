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

        # Substituir todas as variáveis {{coluna}} pelos valores reais do contato
        msg_formatada = mensagem
        for chave, valor in c.items():
            msg_formatada = msg_formatada.replace(f"{{{{{chave}}}}}", str(valor))

        payload = {
            "phone": telefone,
            "image": url_imagem,
            "caption": msg_formatada
        }

        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=20)
            if resp.status_code == 200:
                estado["enviados"] += 1
                nome = c.get("Nome do cliente", c.get("nome", telefone))
                estado["log"].append({"nome": nome, "telefone": telefone, "status": "enviado"})
                ja_enviados.add(telefone)
            else:
                estado["erros"] += 1
                nome = c.get("Nome do cliente", c.get("nome", telefone))
                estado["log"].append({"nome": nome, "telefone": telefone, "status": f"erro_{resp.status_code}"})
        except Exception as e:
            estado["erros"] += 1
            estado["log"].append({"nome": telefone, "telefone": telefone, "status": "excecao"})

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

        # Detectar coluna de telefone automaticamente
        col_tel = None
        for c in df.columns:
            if any(p in c.lower() for p in ['telefone', 'whatsapp', 'celular', 'fone', 'phone']):
                col_tel = c
                break

        if not col_tel:
            return jsonify({"erro": "Nenhuma coluna de telefone encontrada. Use 'Telefone', 'WhatsApp' ou 'Celular'."}), 400

        df[col_tel] = df[col_tel].astype(str).str.replace(r'\D', '', regex=True)
        df = df[df[col_tel].str.len() >= 10]

        progresso = carregar_progresso()
        ja_enviados = set(progresso["enviados"])
        df = df[~df[col_tel].isin(ja_enviados)]

        # Retornar todas as colunas disponíveis (exceto telefone)
        colunas = [c for c in df.columns if c != col_tel]

        # Montar contatos com todos os campos da planilha
        contatos = []
        for _, row in df.iterrows():
            contato = {"telefone": str(row[col_tel])}
            for col in df.columns:
                contato[col] = str(row[col]) if pd.notna(row[col]) else ''
            contatos.append(contato)

        # Preview: primeiros 5
        preview = []
        for c in contatos[:5]:
            nome_col = next((k for k in c if any(p in k.lower() for p in ['nome', 'name', 'cliente'])), None)
            imovel_col = next((k for k in c if any(p in k.lower() for p in ['imóvel', 'imovel', 'empreendimento', 'titulo', 'título'])), None)
            preview.append({
                "nome": c.get(nome_col, '-') if nome_col else '-',
                "telefone": c["telefone"],
                "imovel": c.get(imovel_col, '-') if imovel_col else '-'
            })

        return jsonify({
            "total": len(contatos),
            "colunas": colunas,
            "preview": preview,
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

@app.route("/relatorio-wpp", methods=["POST"])
def relatorio_wpp():
    data = request.json
    mensagem = data.get("mensagem", "Relatório ZapImóvel")

    SEU_NUMERO = "5511911402752"

    headers = {
        "Content-Type": "application/json",
        "client-token": CLIENT_TOKEN
    }

    payload = {
        "phone": SEU_NUMERO,
        "message": mensagem
    }

    url_texto = f"https://api.z-api.io/instances/{INSTANCE_ID}/token/{TOKEN}/send-text"

    try:
        resp = requests.post(url_texto, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            return jsonify({"status": "Relatório enviado!"})
        else:
            return jsonify({"erro": resp.text}), 400
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/resetar", methods=["POST"])
def resetar():
    if os.path.exists(ARQUIVO_PROGRESSO):
        os.remove(ARQUIVO_PROGRESSO)
    return jsonify({"status": "Progresso resetado"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

