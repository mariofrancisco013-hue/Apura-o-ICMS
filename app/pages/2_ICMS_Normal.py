import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button
from lib.db import get_session
from lib.planilha import (
    carregar_itens, salvar_itens_editados, resumo_por_cfop,
    carregar_checkpoint_1024_editavel, salvar_checkpoint_1024_bulk,
    carregar_checkpoint_1025_editavel, salvar_checkpoint_1025_bulk,
)
from lib.calculo_icms_normal import calcular_apuracao_icms_normal, salvar_apuracao
from lib.validacoes import gerar_inconsistencias_ncm, gerar_inconsistencias_transferencia
from sqlalchemy import text

st.set_page_config(page_title="ICMS Normal", layout="wide")
require_login()
logout_button()
st.title("ICMS Normal")

session = get_session()
competencias = session.execute(text("""
    select c.id, e.razao_social, c.ano, c.mes, c.status
    from competencias c join empresas e on e.id = c.empresa_id
    where c.modulo = 'icms_normal'
    order by c.ano desc, c.mes desc
""")).mappings().all()

if not competencias:
    st.info("Nenhuma competência importada ainda. Vá em **Importar Relatórios** primeiro.")
    st.stop()

comp = st.selectbox(
    "Competência", competencias,
    format_func=lambda c: f"{c['razao_social']} — {c['mes']:02d}/{c['ano']} ({c['status']})",
)
cid = comp["id"]

aba_entrada, aba_saida, aba_ajustes, aba_apuracao = st.tabs(
    ["📥 Planilha de Entrada", "📤 Planilha de Saída", "🧮 Ajustes na Apuração", "📋 Apuração"]
)


def _aba_planilha(tipo_operacao, titulo):
    st.caption(
        "Ajuste diretamente na grade (igual planilha) se algum CFOP, NCM ou valor estiver errado no "
        "relatório original. As mudanças só valem para esta competência e não alteram o arquivo .xls."
    )
    c1, c2, c3 = st.columns([2, 3, 2])
    resumo = resumo_por_cfop(session, cid, tipo_operacao)
    cfops_disponiveis = ["(todos)"] + resumo["cfop"].tolist() if not resumo.empty else ["(todos)"]
    cfop_sel = c1.selectbox("Filtrar por CFOP", cfops_disponiveis, key=f"cfop_{tipo_operacao}")
    busca = c2.text_input("Buscar por NF, produto ou parceiro", key=f"busca_{tipo_operacao}")
    limite = c3.number_input("Máx. linhas na tela", min_value=50, max_value=5000, value=500, step=50,
                              key=f"limite_{tipo_operacao}")

    cfop_filtro = None if cfop_sel == "(todos)" else int(cfop_sel)
    df, total = carregar_itens(session, cid, tipo_operacao, cfop_filtro, busca or None, limite)

    if total > len(df):
        st.warning(f"Mostrando {len(df)} de {total} itens (use o filtro de CFOP ou busca para refinar, "
                   f"ou aumente o limite acima — grades muito grandes deixam o navegador lento).")
    else:
        st.caption(f"{total} itens.")

    editado = st.data_editor(
        df, use_container_width=True, height=420, num_rows="fixed", key=f"editor_{tipo_operacao}",
        column_config={"id": st.column_config.NumberColumn("ID", disabled=True)},
    )
    if st.button("💾 Salvar alterações", key=f"salvar_{tipo_operacao}"):
        n = salvar_itens_editados(session, df, editado)
        if n:
            st.success(f"{n} linha(s) atualizada(s).")
        else:
            st.info("Nenhuma mudança detectada.")
        st.rerun()

    st.markdown("---")
    st.subheader("Resumo por CFOP")
    st.dataframe(resumo, use_container_width=True)

    st.markdown("---")
    with st.expander(f"📎 Conferência com a Rotina 1024 ({'Entrada' if tipo_operacao=='entrada' else 'Saída'})"):
        st.caption(
            "Preencha as colunas 'base_1024' e 'icms_1024' com os valores da Rotina 1024 (RAICMS) para "
            "cada CFOP — dá para colar direto de uma planilha/PDF. As colunas 'diff' mostram a diferença "
            "contra o que foi calculado a partir do relatório importado."
        )
        ref = carregar_checkpoint_1024_editavel(session, cid)
        ref = ref[ref["cfop"].isin(resumo["cfop"])] if not resumo.empty else ref
        ref_editado = st.data_editor(
            ref, use_container_width=True, key=f"checkpoint1024_{tipo_operacao}",
            column_config={
                "cfop": st.column_config.NumberColumn("CFOP", disabled=True),
                "descricao": st.column_config.TextColumn("Descrição", disabled=True),
                "base_calc": st.column_config.NumberColumn("Base (calculado)", disabled=True, format="%.2f"),
                "icms_calc": st.column_config.NumberColumn("ICMS (calculado)", disabled=True, format="%.2f"),
                "base_1024": st.column_config.NumberColumn("Base (Rotina 1024)", format="%.2f"),
                "icms_1024": st.column_config.NumberColumn("ICMS (Rotina 1024)", format="%.2f"),
            },
        )
        if st.button("Salvar valores da Rotina 1024", key=f"salvar_1024_{tipo_operacao}"):
            n = salvar_checkpoint_1024_bulk(session, cid, ref_editado)
            st.success(f"{n} CFOP(s) salvo(s).")
            st.rerun()

        diffs = ref_editado.assign(
            diff_base=ref_editado["base_calc"] - ref_editado["base_1024"],
            diff_icms=ref_editado["icms_calc"] - ref_editado["icms_1024"],
        )
        divergentes = diffs[(diffs["diff_base"].abs() > 0.05) | (diffs["diff_icms"].abs() > 0.05)]
        if not divergentes.empty:
            st.warning(f"{len(divergentes)} CFOP(s) com diferença acima de R$ 0,05:")
            st.dataframe(divergentes[["cfop", "descricao", "diff_base", "diff_icms"]], use_container_width=True)
        elif ref_editado["base_1024"].notna().any():
            st.success("Tudo bateu com os valores da Rotina 1024 informados.")


with aba_entrada:
    _aba_planilha("entrada", "Entrada")

with aba_saida:
    _aba_planilha("saida", "Saída")

with aba_ajustes:
    st.caption(
        "Informações que NÃO vêm dos relatórios de Entrada/Saída: DIFAL (débito), CIAP e DAE Antecipado "
        "(crédito), e lançamentos de CFOP que o sistema contábil registra direto (ex: CFOP 1602) sem "
        "passar pelo relatório de NF."
    )
    lancamentos = session.execute(text("""
        select id, tipo, cfop_relacionado, descricao, valor from lancamentos_manuais
        where competencia_id = :cid order by tipo, id
    """), {"cid": cid}).mappings().all()

    col_deb, col_cred = st.columns(2)
    with col_deb:
        st.subheader("Débitos")
        deb = [l for l in lancamentos if l["tipo"] in ("difal_debito", "ajuste_cfop_debito")]
        st.dataframe(deb, use_container_width=True)
    with col_cred:
        st.subheader("Créditos")
        cred = [l for l in lancamentos if l["tipo"] in ("ciap_credito", "dae_antecipado_credito", "ajuste_cfop_credito")]
        st.dataframe(cred, use_container_width=True)

    with st.form("novo_lancamento", clear_on_submit=True):
        st.markdown("**Adicionar lançamento**")
        c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
        tipo = c1.selectbox("Tipo", [
            "difal_debito", "ciap_credito", "dae_antecipado_credito",
            "ajuste_cfop_credito", "ajuste_cfop_debito", "outro",
        ], help=(
            "difal_debito: DIFAL a recolher. ciap_credito: crédito do CIAP do mês (anexo/arquivo CIAP). "
            "dae_antecipado_credito: DAE de ICMS Antecipado pago no período. ajuste_cfop_credito/debito: "
            "CFOP lançado direto no contábil, fora do relatório de NF (ex: CFOP 1602)."
        ))
        descricao = c2.text_input("Descrição (ex: 'CIAP mês 07/2026', 'DAE 202642354711255', 'CFOP 1602')")
        valor = c3.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f")
        cfop_rel = c4.number_input("CFOP relacionado (se aplicável)", min_value=0, max_value=6999, step=1)
        if st.form_submit_button("Adicionar"):
            session.execute(text("""
                insert into lancamentos_manuais (competencia_id, tipo, cfop_relacionado, descricao, valor)
                values (:cid, :tipo, :cfop, :desc, :valor)
            """), {"cid": cid, "tipo": tipo, "cfop": cfop_rel or None, "desc": descricao, "valor": valor})
            session.commit()
            st.rerun()

with aba_apuracao:
    st.caption(
        "Calcula as linhas 01-14 a partir da Planilha de Entrada/Saída (já com os ajustes que você fez) + "
        "os lançamentos manuais da aba Ajustes. O resultado abaixo é organizado como a Rotina 1025."
    )
    if st.button("🧮 Calcular apuração", type="primary"):
        with st.spinner("Calculando..."):
            linhas = calcular_apuracao_icms_normal(session, cid)
            salvar_apuracao(session, cid, linhas)
            n_ncm = gerar_inconsistencias_ncm(session, cid)
            n_transf = gerar_inconsistencias_transferencia(session, cid)
            session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
            session.commit()
        st.success(f"Calculado. {n_ncm} inconsistência(s) de NCM e {n_transf} de transferência geradas — "
                   f"veja a página **Inconsistências**.")
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

        st.markdown("#### DÉBITO DO IMPOSTO")
        st.table(pd.DataFrame([
            {"Linha": "01", "Descrição": "Por Saídas com débito", "Valor": f"R$ {_linha('01'):,.2f}"},
            {"Linha": "02", "Descrição": "Outros Débitos", "Valor": f"R$ {_linha('02'):,.2f}"},
            {"Linha": "03", "Descrição": "Estorno de Créditos", "Valor": f"R$ {_linha('03'):,.2f}"},
            {"Linha": "04", "Descrição": "Subtotal Débito", "Valor": f"R$ {_linha('04'):,.2f}"},
        ]).set_index("Linha"))

        st.markdown("#### CRÉDITO DO IMPOSTO")
        st.table(pd.DataFrame([
            {"Linha": "05", "Descrição": "Por Entradas com crédito", "Valor": f"R$ {_linha('05'):,.2f}"},
            {"Linha": "06", "Descrição": "Outros Créditos", "Valor": f"R$ {_linha('06'):,.2f}"},
            {"Linha": "07", "Descrição": "Estorno de Débitos", "Valor": f"R$ {_linha('07'):,.2f}"},
            {"Linha": "08", "Descrição": "Subtotal Crédito", "Valor": f"R$ {_linha('08'):,.2f}"},
        ]).set_index("Linha"))

        st.markdown("#### APURAÇÃO DO SALDO")
        st.table(pd.DataFrame([
            {"Linha": "09", "Descrição": "Saldo Credor do Período Anterior", "Valor": f"R$ {_linha('09'):,.2f}"},
            {"Linha": "11", "Descrição": "Saldo Devedor (Débito menos Crédito)", "Valor": f"R$ {_linha('11'):,.2f}"},
            {"Linha": "12", "Descrição": "Deduções", "Valor": f"R$ {_linha('12'):,.2f}"},
            {"Linha": "13", "Descrição": "Imposto a Recolher", "Valor": f"R$ {_linha('13'):,.2f}"},
            {"Linha": "14", "Descrição": "Saldo Credor a Transportar", "Valor": f"R$ {_linha('14'):,.2f}"},
        ]).set_index("Linha"))

        st.markdown("---")
        with st.expander("📎 Conferência com a Rotina 1025 (livro completo)"):
            st.caption("Preencha 'valor_1025' com o valor de cada linha do livro fiscal oficial.")
            ref = carregar_checkpoint_1025_editavel(session, cid)
            ref_editado = st.data_editor(
                ref, use_container_width=True, key="checkpoint1025",
                column_config={
                    "linha": st.column_config.TextColumn("Linha", disabled=True),
                    "descricao": st.column_config.TextColumn("Descrição", disabled=True),
                    "valor_calc": st.column_config.NumberColumn("Calculado", disabled=True, format="%.2f"),
                    "valor_1025": st.column_config.NumberColumn("Rotina 1025", format="%.2f"),
                },
            )
            if st.button("Salvar valores da Rotina 1025"):
                n = salvar_checkpoint_1025_bulk(session, cid, ref_editado)
                st.success(f"{n} linha(s) salva(s).")
                st.rerun()

            diffs = ref_editado.assign(diff=ref_editado["valor_calc"] - ref_editado["valor_1025"])
            divergentes = diffs[diffs["diff"].abs() > 0.05]
            if not divergentes.empty:
                st.warning(f"{len(divergentes)} linha(s) com diferença acima de R$ 0,05:")
                st.dataframe(divergentes[["linha", "descricao", "diff"]], use_container_width=True)
            elif ref_editado["valor_1025"].notna().any():
                st.success("Apuração bate com a Rotina 1025.")
