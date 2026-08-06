import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button
from lib.db import get_session
from sqlalchemy import text

st.set_page_config(page_title="Importar Relatórios", layout="wide")
require_login()
logout_button()
st.title("Importar Relatórios")

session = get_session()
empresas = session.execute(text("select id, razao_social, cnpj from empresas order by razao_social")).mappings().all()
if not empresas:
    st.warning("Nenhuma empresa cadastrada ainda. Cadastre em **Empresas** antes de importar.")
    st.stop()

col1, col2, col3 = st.columns(3)
empresa = col1.selectbox("Empresa", empresas, format_func=lambda e: f"{e['razao_social']} ({e['cnpj']})")
ano = col2.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1)
mes = col3.number_input("Mês", min_value=1, max_value=12, value=7, step=1)

st.markdown("---")
arq_entrada = st.file_uploader("Relatório de Entrada (.xls)", type=["xls"])
arq_saida = st.file_uploader("Relatório de Saída (.xls)", type=["xls"])

comp = session.execute(text("""
    select id from competencias where empresa_id=:eid and ano=:ano and mes=:mes and modulo='icms_normal'
"""), {"eid": empresa["id"], "ano": ano, "mes": mes}).fetchone()

# Conta Entrada e Saída separadamente: reimportar um dos dois não deve mexer no outro (bug corrigido em
# 06/08/2026 — antes a checagem/exclusão de "substituir" tratava os dois tipos juntos, então corrigir a
# Saída apagava a Entrada já importada, ou ficava bloqueado por causa dela sem jeito de contornar).
n_entrada = n_saida = 0
if comp:
    contagem = dict(session.execute(text("""
        select tipo_operacao, count(*) from notas_fiscais_itens where competencia_id=:cid
        group by tipo_operacao
    """), {"cid": comp[0]}).all())
    n_entrada = contagem.get("entrada", 0)
    n_saida = contagem.get("saida", 0)

tipos_neste_envio = []
if arq_entrada:
    tipos_neste_envio.append("entrada")
if arq_saida:
    tipos_neste_envio.append("saida")
ja_importado_no_envio = (arq_entrada and n_entrada > 0) or (arq_saida and n_saida > 0)

if n_entrada or n_saida:
    st.caption(f"Já importados nesta competência: **{n_entrada}** itens de Entrada, **{n_saida}** itens de Saída.")
if ja_importado_no_envio:
    partes_aviso = []
    if arq_entrada and n_entrada:
        partes_aviso.append(f"Entrada ({n_entrada} itens)")
    if arq_saida and n_saida:
        partes_aviso.append(f"Saída ({n_saida} itens)")
    st.warning(f"Esta competência já tem itens importados de: {', '.join(partes_aviso)}. Marque a opção "
               f"abaixo para substituir (reimportação de relatório corrigido) — sem isso, a importação "
               f"desses arquivos é bloqueada para evitar duplicar notas fiscais. Isso só afeta o(s) tipo(s) "
               f"que você está enviando agora — o outro tipo (se já importado) não é alterado.")

substituir = st.checkbox("Substituir importação existente desta competência (só do(s) arquivo(s) enviados agora)",
                          value=False, disabled=not ja_importado_no_envio)

if st.button("Importar", type="primary", disabled=not (arq_entrada or arq_saida)):
    from lib.importacao import importar
    with st.spinner("Importando..."):
        try:
            resultado = importar(session, empresa["cnpj"], ano, mes, arq_entrada, arq_saida, substituir)
            st.success(resultado)
            st.rerun()
        except ValueError as e:
            st.error(str(e))
