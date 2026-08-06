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
from lib.ncm_tributado import (
    listar_ncms_tributados, salvar_ncms_tributados, gerar_inconsistencias_ncm_tributado,
)
from lib.importar_1024 import parse_rotina_1024
from lib.formatacao import formatar_moeda, coluna_moeda
from sqlalchemy import text

st.set_page_config(page_title="ICMS Normal", layout="wide")
require_login()
logout_button()
st.title("ICMS Normal")

session = get_session()
competencias = session.execute(text("""
    select c.id, e.id as empresa_id, e.razao_social, c.ano, c.mes, c.status
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
empresa_id = comp["empresa_id"]

aba_entrada, aba_saida, aba_ncm, aba_ajustes, aba_apuracao = st.tabs([
    "📥 Planilha de Entrada", "📤 Planilha de Saída", "🔖 NCMs Tributados",
    "🧮 Ajustes na Apuração", "📋 Apuração",
])


def _formatar_moeda_df(df, colunas):
    """Devolve uma cópia do DataFrame com as colunas indicadas formatadas como texto 'R$ 1.234,56' — só
    para exibição (st.dataframe/st.table), nunca para grades editáveis (ali a coluna precisa continuar
    numérica, ver lib/formatacao.py)."""
    if df.empty:
        return df
    out = df.copy()
    for c in colunas:
        if c in out.columns:
            out[c] = out[c].apply(formatar_moeda)
    return out


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
        column_order=["id", "nf_numero", "parceiro", "produto", "ncm", "ncm_descricao", "cfop",
                      "valor_produto", "aliq_icms", "base_icms", "valor_icms", "uf"],
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "ncm_descricao": st.column_config.TextColumn(
                "O que é este NCM", disabled=True, width="large",
                help="Descrição oficial da Tabela NCM (Receita Federal/Classif), só para consulta — não é "
                     "gravada, vem de um cruzamento automático com o código NCM."
            ),
            "valor_produto": coluna_moeda("Valor Produto"),
            "base_icms": coluna_moeda("Base ICMS"),
            "valor_icms": coluna_moeda("Valor ICMS"),
        },
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
    st.dataframe(_formatar_moeda_df(resumo, ["base", "icms"]), use_container_width=True)

    st.markdown("---")
    with st.expander(f"📎 Conferência com a Rotina 1024 ({'Entrada' if tipo_operacao=='entrada' else 'Saída'})"):
        st.caption(
            "Anexe o PDF da Rotina 1024 (Livro RAICMS Modelo P9) e clique em Importar — preenche as "
            "colunas 'base_1024'/'icms_1024' de todos os CFOPs automaticamente, sem digitar. Se preferir, "
            "também dá para editar/colar valor a valor direto na grade abaixo. Mostra TODO CFOP que "
            "aparecer no relatório importado OU na Rotina 1024, mesmo que só num dos dois — é assim que "
            "aparecem CFOPs como o 1602 (lançado direto no sistema contábil, sem passar por nota fiscal)."
        )
        c_up1, c_up2 = st.columns([3, 1])
        pdf_1024 = c_up1.file_uploader(
            "PDF da Rotina 1024", type=["pdf"], key=f"upload_1024_{tipo_operacao}",
            label_visibility="collapsed",
        )
        if c_up2.button("📥 Importar do PDF", key=f"importar_1024_{tipo_operacao}", disabled=pdf_1024 is None):
            try:
                linhas_1024 = parse_rotina_1024(pdf_1024)
                df_1024 = pd.DataFrame(linhas_1024).rename(
                    columns={"valor_base": "base_1024", "valor_icms": "icms_1024"}
                )
                n = salvar_checkpoint_1024_bulk(session, cid, df_1024)
                st.success(f"{n} CFOP(s) importado(s) do PDF da Rotina 1024 (Entrada + Saída juntas).")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

        ref = carregar_checkpoint_1024_editavel(session, cid, tipo_operacao)
        ref_editado = st.data_editor(
            ref, use_container_width=True, key=f"checkpoint1024_{tipo_operacao}",
            column_config={
                "cfop": st.column_config.NumberColumn("CFOP", disabled=True),
                "descricao": st.column_config.TextColumn("Descrição", disabled=True),
                "base_calc": coluna_moeda("Base (calculado)", disabled=True),
                "icms_calc": coluna_moeda("ICMS (calculado)", disabled=True),
                "base_1024": coluna_moeda("Base (Rotina 1024)"),
                "icms_1024": coluna_moeda("ICMS (Rotina 1024)"),
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
            st.dataframe(
                _formatar_moeda_df(divergentes[["cfop", "descricao", "diff_base", "diff_icms"]],
                                    ["diff_base", "diff_icms"]),
                use_container_width=True,
            )
        elif ref_editado["base_1024"].notna().any():
            st.success("Tudo bateu com os valores da Rotina 1024 informados.")


with aba_entrada:
    _aba_planilha("entrada", "Entrada")

with aba_saida:
    _aba_planilha("saida", "Saída")

with aba_ncm:
    st.markdown(
        "**Para que serve esta aba:** cadastro dos NCMs que você sabe que são \"de fato tributados\" "
        "(não-ST — geram crédito/débito pleno de ICMS), esperados nos CFOPs 1102, 1202, 5102, 6102 e "
        "5927. É por empresa porque o mesmo NCM pode ter tratamento diferente dependendo da empresa/UF. "
        "A partir desse cadastro, ao clicar em **Calcular apuração** (aba Apuração) o sistema sinaliza "
        "duas coisas, sempre para você revisar — ele nunca decide sozinho:"
    )
    st.markdown(
        "- Um NCM que **está** cadastrado aqui mas apareceu num item classificado como **ST** — pode ser "
        "erro de CFOP no lançamento, ou o produto pode ter deixado de ser tributado.\n"
        "- Um NCM que **não está** cadastrado mas apareceu como **tributado (não-ST)** — um candidato "
        "novo para você confirmar se deve entrar na lista."
    )
    st.caption(
        "Essas sinalizações aparecem na página **Inconsistências** (tipos 'ncm_tributado_como_st' e "
        "'ncm_tributado_novo')."
    )

    ncms_df = listar_ncms_tributados(session, empresa_id)
    st.caption(f"{len(ncms_df)} NCM(s) cadastrado(s) para {comp['razao_social']}. A coluna \"Descrição "
               "oficial\" vem da Tabela NCM da Receita Federal automaticamente — só digite algo em "
               "\"Observação\" se quiser anotar algo próprio.")
    ncms_editado = st.data_editor(
        ncms_df, use_container_width=True, num_rows="dynamic", key="editor_ncms_tributados",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "ncm": st.column_config.TextColumn("NCM", required=True),
            "descricao_oficial": st.column_config.TextColumn(
                "Descrição oficial (Tabela NCM)", disabled=True, width="large"
            ),
            "descricao": st.column_config.TextColumn("Observação (opcional)"),
            "created_at": st.column_config.DatetimeColumn("Cadastrado em", disabled=True),
        },
        column_order=["ncm", "descricao_oficial", "descricao", "created_at", "id"],
    )
    st.caption("Para incluir: adicione uma linha nova (ícone + no final da grade) e digite o NCM. "
               "Para excluir: selecione a linha e apague (ícone de lixeira). Depois clique em Salvar.")
    if st.button("💾 Salvar lista de NCMs tributados"):
        resultado = salvar_ncms_tributados(session, empresa_id, ncms_df, ncms_editado)
        st.success(f"{resultado['incluidos']} incluído(s), {resultado['removidos']} removido(s).")
        st.rerun()

with aba_ajustes:
    st.markdown(
        "**Direcionador de ajustes manuais desta competência.** Cada lançamento aqui é uma informação que "
        "NÃO veio pronta dos relatórios de Entrada/Saída e precisou ser incluída à mão para a apuração "
        "bater com o livro fiscal oficial — DIFAL (débito), CIAP e DAE Antecipado (crédito), e CFOPs que o "
        "sistema contábil lança direto, sem passar por nota fiscal (ex: CFOP 1602). Se o mesmo tipo de "
        "ajuste se repetir todo mês, é um sinal de que vale a pena corrigir na origem (Winthor) para não "
        "precisar mais lançar manualmente aqui."
    )
    lancamentos = session.execute(text("""
        select id, tipo, cfop_relacionado, descricao, valor from lancamentos_manuais
        where competencia_id = :cid order by tipo, id
    """), {"cid": cid}).mappings().all()

    col_deb, col_cred = st.columns(2)
    with col_deb:
        st.subheader("Débitos")
        deb = [dict(l, valor=formatar_moeda(l["valor"])) for l in lancamentos
               if l["tipo"] in ("difal_debito", "ajuste_cfop_debito")]
        st.dataframe(deb, use_container_width=True)
    with col_cred:
        st.subheader("Créditos")
        cred = [dict(l, valor=formatar_moeda(l["valor"])) for l in lancamentos
                if l["tipo"] in ("ciap_credito", "dae_antecipado_credito", "ajuste_cfop_credito")]
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
            n_ncm_trib = gerar_inconsistencias_ncm_tributado(session, cid, empresa_id)
            session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
            session.commit()
        st.success(
            f"Calculado. {n_ncm} inconsistência(s) de NCM x ST, {n_transf} de transferência e "
            f"{n_ncm_trib} de NCM tributado geradas — veja a página **Inconsistências**."
        )
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
            {"Linha": "01", "Descrição": "Por Saídas com débito", "Valor": formatar_moeda(_linha('01'))},
            {"Linha": "02", "Descrição": "Outros Débitos", "Valor": formatar_moeda(_linha('02'))},
            {"Linha": "03", "Descrição": "Estorno de Créditos", "Valor": formatar_moeda(_linha('03'))},
            {"Linha": "04", "Descrição": "Subtotal Débito", "Valor": formatar_moeda(_linha('04'))},
        ]).set_index("Linha"))

        st.markdown("#### CRÉDITO DO IMPOSTO")
        st.table(pd.DataFrame([
            {"Linha": "05", "Descrição": "Por Entradas com crédito", "Valor": formatar_moeda(_linha('05'))},
            {"Linha": "06", "Descrição": "Outros Créditos", "Valor": formatar_moeda(_linha('06'))},
            {"Linha": "07", "Descrição": "Estorno de Débitos", "Valor": formatar_moeda(_linha('07'))},
            {"Linha": "08", "Descrição": "Subtotal Crédito", "Valor": formatar_moeda(_linha('08'))},
        ]).set_index("Linha"))

        st.markdown("#### APURAÇÃO DO SALDO")
        st.table(pd.DataFrame([
            {"Linha": "09", "Descrição": "Saldo Credor do Período Anterior", "Valor": formatar_moeda(_linha('09'))},
            {"Linha": "11", "Descrição": "Saldo Devedor (Débito menos Crédito)", "Valor": formatar_moeda(_linha('11'))},
            {"Linha": "12", "Descrição": "Deduções", "Valor": formatar_moeda(_linha('12'))},
            {"Linha": "13", "Descrição": "Imposto a Recolher", "Valor": formatar_moeda(_linha('13'))},
            {"Linha": "14", "Descrição": "Saldo Credor a Transportar", "Valor": formatar_moeda(_linha('14'))},
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
                    "valor_calc": coluna_moeda("Calculado", disabled=True),
                    "valor_1025": coluna_moeda("Rotina 1025"),
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
                st.dataframe(
                    _formatar_moeda_df(divergentes[["linha", "descricao", "diff"]], ["diff"]),
                    use_container_width=True,
                )
            elif ref_editado["valor_1025"].notna().any():
                st.success("Apuração bate com a Rotina 1025.")
