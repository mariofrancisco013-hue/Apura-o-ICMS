import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.importacao import get_or_create_competencia
from lib.importar_1024 import parse_rotina_1024
from lib.extrato_antecipado_pe import (
    parse_extrato_antecipado, salvar_extrato_antecipado, listar_extrato_antecipado,
)
from lib.cfops_antecipacao_pe import listar_cfops_antecipacao, salvar_cfops_antecipacao
from lib.calculo_icms_pe import (
    salvar_checkpoint_1024_pe, carregar_checkpoint_1024_pe,
    sugerir_valor_4101, carregar_valor_4101_manual, salvar_valor_4101_manual,
    calcular_apuracao_pe,
)
from lib.calculo_icms_normal import salvar_apuracao
from lib.formatacao import formatar_moeda, coluna_moeda
from sqlalchemy import text

st.set_page_config(page_title="ICMS PE", layout="wide")
require_login()
logout_button()
st.title("ICMS PE — Crédito Presumido")
st.caption(
    "Apuração do regime de Crédito Presumido do atacadista (Decreto de PE) — modelo diferente do ICMS "
    "Normal: em vez de débito/crédito por CFOP, calcula Antecipação (imposto na entrada) + Crédito "
    "Presumido (benefício sobre a base de saídas). Fontes: Rotina 1024 (mesmo PDF do ICMS Normal) + "
    "Extrato de ICMS Antecipado do e-Fisco/PE."
)

session = get_session()

empresas = session.execute(text("select id, razao_social, cnpj from empresas order by razao_social")).mappings().all()
if not empresas:
    st.warning("Nenhuma empresa cadastrada ainda. Cadastre em **Empresas** antes de continuar.")
    st.stop()

col1, col2, col3 = st.columns(3)
empresa = col1.selectbox("Empresa", empresas, format_func=lambda e: f"{e['razao_social']} ({e['cnpj']})")
ano = col2.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1)
mes = col3.number_input("Mês", min_value=1, max_value=12, value=6, step=1)

cid = get_or_create_competencia(session, empresa["cnpj"], ano, mes, modulo="icms_antecipado")
empresa_id = empresa["id"]
status = session.execute(text("select status from competencias where id = :cid"), {"cid": cid}).scalar()
st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** (status: {status}).")

(aba_1024, aba_extrato, aba_cfops, aba_apuracao) = st.tabs([
    "📥 Rotina 1024", "📄 Extrato de ICMS Antecipado", "🔖 CFOPs de Antecipação", "📋 Apuração",
])

# ============================================================================================
with aba_1024:
    st.caption(
        "Mesmo PDF já usado no ICMS Normal (Livro RAICMS Modelo P9) — aqui reaproveitamos as colunas "
        "\"Valores Contábeis\", \"Base de Cálculo\" e \"Imposto Creditado/Debitado\" de todos os CFOPs de "
        "uma vez, sem digitar valor a valor."
    )
    c_up1, c_up2 = st.columns([3, 1])
    pdf_1024 = c_up1.file_uploader("PDF da Rotina 1024", type=["pdf"], key="upload_1024_pe",
                                    label_visibility="collapsed")
    if c_up2.button("📥 Importar do PDF", key="importar_1024_pe", disabled=pdf_1024 is None):
        try:
            linhas_1024 = parse_rotina_1024(pdf_1024)
            n = salvar_checkpoint_1024_pe(session, cid, linhas_1024)
            st.success(f"{n} CFOP(s) importado(s) do PDF da Rotina 1024 (Entrada + Saída juntas).")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    df_1024 = carregar_checkpoint_1024_pe(session, cid)
    if df_1024.empty:
        st.info("Nenhum dado da Rotina 1024 importado ainda para esta competência.")
    else:
        st.dataframe(
            df_1024.assign(
                valor_contabil=df_1024["valor_contabil"].apply(formatar_moeda),
                valor_base=df_1024["valor_base"].apply(formatar_moeda),
                valor_icms=df_1024["valor_icms"].apply(formatar_moeda),
            ),
            use_container_width=True,
        )

# ============================================================================================
with aba_extrato:
    st.caption(
        "Extrato de Notas Fiscais Relativas a Operações Interestaduais Sujeitas ao ICMS Antecipado "
        "(e-Fisco/PE) — usa o quadro \"Resumo do Grupo de Mercadorias para Extrato dos itens COBRADOS\". "
        "A linha 3.2 da Apuração (Antecipação fora do estado) soma o \"ICMS Devido\" só dos grupos com "
        "\"Direito a Crédito\" = Sim."
    )
    c_up1, c_up2 = st.columns([3, 1])
    pdf_extrato = c_up1.file_uploader("PDF do Extrato de ICMS Antecipado", type=["pdf"],
                                       key="upload_extrato_pe", label_visibility="collapsed")
    if c_up2.button("📥 Importar do PDF", key="importar_extrato_pe", disabled=pdf_extrato is None):
        try:
            grupos = parse_extrato_antecipado(pdf_extrato)
            n = salvar_extrato_antecipado(session, cid, grupos)
            st.success(f"{n} grupo(s) de mercadoria importado(s) do Extrato.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    df_extrato = listar_extrato_antecipado(session, cid)
    if df_extrato.empty:
        st.info("Nenhum Extrato de ICMS Antecipado importado ainda para esta competência.")
    else:
        st.dataframe(
            df_extrato.assign(icms_devido=df_extrato["icms_devido"].apply(formatar_moeda)),
            use_container_width=True,
        )
        total = df_extrato.loc[df_extrato["direito_credito"], "icms_devido"].sum()
        st.metric("Total com Direito a Crédito (= linha 3.2)", formatar_moeda(total))

# ============================================================================================
with aba_cfops:
    st.markdown(
        "**Para que serve esta aba:** cadastro dos CFOPs de Entrada que compõem a base da Antecipação, "
        "por empresa — \"interna\" soma na linha 3.1 (calculada a 1,1% do total), \"externa\" soma na "
        "linha 3.2.1 (valor de referência/auditoria; o valor real da 3.2 vem do Extrato do e-Fisco). Um "
        "cadastro inicial já vem pré-carregado a partir da planilha de apuração real, mas pode editar."
    )
    cfops_df = listar_cfops_antecipacao(session, empresa_id)
    st.caption(f"{len(cfops_df)} CFOP(s) cadastrado(s) para {empresa['razao_social']}.")
    cfops_editado = st.data_editor(
        cfops_df, use_container_width=True, num_rows="dynamic", key="editor_cfops_antecipacao_pe",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "cfop": st.column_config.NumberColumn("CFOP", required=True),
            "descricao": st.column_config.TextColumn("Descrição", disabled=True, width="large"),
            "bucket": st.column_config.SelectboxColumn("Bucket", options=["interna", "externa"], required=True),
            "observacao": st.column_config.TextColumn("Observação (opcional)", width="large"),
            "criado_por_email": st.column_config.TextColumn("Cadastrado por", disabled=True),
            "created_at": st.column_config.DatetimeColumn("Cadastrado em", disabled=True),
        },
        column_order=["cfop", "descricao", "bucket", "observacao", "criado_por_email", "created_at", "id"],
    )
    st.caption("Para incluir: adicione uma linha nova (ícone + no final da grade), digite o CFOP e escolha "
               "o bucket. Para excluir: selecione a linha e apague (ícone de lixeira). Depois clique em Salvar.")
    if st.button("💾 Salvar CFOPs de Antecipação"):
        resultado = salvar_cfops_antecipacao(session, empresa_id, cfops_df, cfops_editado, usuario=usuario_atual())
        st.success(f"{resultado['incluidos']} incluído(s), {resultado['removidos']} removido(s).")
        st.rerun()

# ============================================================================================
with aba_apuracao:
    st.markdown("#### Linha 4.1.01 — Valor Total das Saídas ajustado")
    st.caption(
        "Única linha da apuração que não dá para calcular com 100% de certeza só com os dados da Rotina "
        "1024: é o total de Valores Contábeis das Saídas, menos 'outras saídas' e devoluções de compra, "
        "menos uma eventual reclassificação contábil que não aparece em nenhum CFOP do relatório. Por "
        "isso é um campo editável — a sugestão abaixo é só um ponto de partida, confira/ajuste contra seu "
        "controle interno antes de calcular."
    )
    try:
        sugestao = sugerir_valor_4101(session, cid)
    except Exception:
        sugestao = None
    valor_salvo = carregar_valor_4101_manual(session, cid)
    valor_inicial = float(valor_salvo) if valor_salvo is not None else (float(sugestao) if sugestao else 0.0)
    if sugestao is not None:
        st.caption(f"Sugestão calculada (sem a reclassificação manual): {formatar_moeda(sugestao)}.")
    c_41, c_42 = st.columns([2, 1])
    valor_4101 = c_41.number_input("Valor da linha 4.1.01 (R$)", value=valor_inicial, step=0.01, format="%.2f")
    if c_42.button("💾 Salvar valor manual"):
        salvar_valor_4101_manual(session, cid, valor_4101)
        st.success("Valor salvo.")
        st.rerun()

    st.markdown("---")
    if st.button("🧮 Calcular apuração", type="primary"):
        with st.spinner("Calculando..."):
            linhas = calcular_apuracao_pe(session, cid, empresa_id, int(ano), int(mes),
                                           valor_4101_manual=valor_4101)
            salvar_apuracao(session, cid, linhas)
            session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
            session.commit()
        st.success("Calculado.")
        st.rerun()

    linhas_db = {r["linha"]: r for r in session.execute(text("""
        select linha, descricao, valor from apuracao_linhas where competencia_id = :cid
    """), {"cid": cid}).mappings().all()}

    if not linhas_db:
        st.info("Ainda não calculado.")
    else:
        def _linha(cod):
            r = linhas_db.get(cod)
            return r["valor"] if r else 0

        def _tabela(pares):
            return pd.DataFrame([
                {"Linha": cod, "Descrição": desc, "Valor": formatar_moeda(_linha(cod))}
                for cod, desc in pares
            ]).set_index("Linha")

        st.markdown("#### 3. ANTECIPAÇÃO")
        st.table(_tabela([
            ("3", "Antecipação (3.1 + 3.2)"),
            ("3.1", "Antecipação 1,1% dentro do estado"),
            ("3.1.1", "Total base Entrada/Antecipação interna"),
            ("3.2", "Antecipação fora do estado (Extrato e-Fisco)"),
            ("3.2.1", "Total base Entrada externa (referência)"),
        ]))

        st.markdown("#### 1. CRÉDITOS TOTAIS")
        st.table(_tabela([
            ("1", "Créditos Totais"),
            ("1.1", "Créditos Entradas - Devoluções"),
            ("1.2", "Crédito 1,1% recolhido no mês anterior"),
            ("1.3", "Crédito 6% recolhido no mês anterior"),
            ("1.4", "Estorno de débitos - Devoluções de vendas (não-ST)"),
            ("1.5", "Estorno de débitos - Devoluções de vendas ST (Outros)"),
        ]))

        st.markdown("#### 2. DÉBITOS TOTAIS")
        st.table(_tabela([
            ("2", "Débitos Totais"),
            ("2.1", "Débito Saídas"),
            ("2.2", "Estorno de crédito - Devolução de compras"),
            ("2.3", "Débito Transferências"),
        ]))

        st.markdown("#### 4. CRÉDITO PRESUMIDO")
        st.table(_tabela([
            ("4", "Crédito Presumido (4.1 x 4.2 - 4.3)"),
            ("4.1", "Alíquota Média (4.1.02/4.1.01)"),
            ("4.1.01", "Valor Total das Saídas ajustado"),
            ("4.1.02", "Valor Total dos débitos"),
            ("4.2", "Base de cálculo Crédito Presumido"),
            ("4.2.01", "Aquisições - Devoluções - Serviços - Remessas"),
            ("4.2.02", "Adicional 35% Aquisições"),
            ("4.3", "Deduções Demais Créditos"),
            ("4.3.01", "Antecipação"),
            ("4.3.02", "Crédito Entradas"),
        ]))

        st.markdown("#### 5/6/7. RECOLHIMENTO")
        st.table(_tabela([
            ("5", "Valor a Recolher (2. - 1. - 4.)"),
            ("6", "Saldo Crédito Anterior"),
            ("7", "Valor Recolher Atual (5. - 6.)"),
        ]))

        valor_7 = _linha("7")
        if valor_7 and valor_7 < 0:
            st.success(f"Saldo credor a transportar para o mês seguinte: {formatar_moeda(-valor_7)}.")
        elif valor_7:
            st.warning(f"Valor a recolher neste mês: {formatar_moeda(valor_7)}.")
